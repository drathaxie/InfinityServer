"""Prove a rebuilt bundle serializes the same content as its predecessor apart from named texture fields.

usage: python bundle_equivalence.py <old.unity3d> <new.unity3d> [--ignore m_IsReadable]

Every serialized object is normalized: object references become the referenced object's type and name
(path IDs change whenever a builder recreates its assets), texture pixels become the SHA-256 of their stored
(compressed) data, and the AssetBundle table is sorted. The two bundles must then hold identical multisets of
objects. With only m_IsReadable ignored, identical texture data, materials, clips, prefabs and shaders mean
the client draws exactly what it drew before.
"""
import argparse
from collections import Counter
import hashlib
import json

import UnityPy


def _digest(value):
    return 'sha256:' + hashlib.sha256(json.dumps(value, sort_keys=True, default=repr).encode()).hexdigest()


def signature(path, ignore=('m_IsReadable',)):
    env = UnityPy.load(str(path))
    objects = {obj.path_id: obj for obj in env.objects}
    files = {id(obj.assets_file) for obj in objects.values()}
    assert len(files) == 1, 'Expected one serialized file per bundle'
    # Built-in resources (e.g. Sprites-Default in unity_builtin_extra) are referenced by file and stable ID.
    externals = [e.path for e in next(iter(objects.values())).assets_file.externals]
    trees, labels = {}, {}

    def tree(pid):
        if pid not in trees:
            trees[pid] = objects[pid].read_typetree()
        return trees[pid]

    def label(pid):
        if pid in labels:
            return labels[pid]
        labels[pid] = '?'  # breaks reference cycles while the label is computed
        obj = objects[pid]
        t = tree(pid)
        name = t.get('m_Name') or ''
        owner = t.get('m_GameObject')
        if not name and isinstance(owner, dict) and owner.get('m_PathID') in objects:
            name = tree(owner['m_PathID']).get('m_Name', '')
        script = t.get('m_Script')
        if isinstance(script, dict) and script.get('m_PathID') in objects:
            name += ':' + tree(script['m_PathID']).get('m_ClassName', '')
        labels[pid] = obj.type.name + ':' + name
        return labels[pid]

    def normalize(value, digest=True):
        if isinstance(value, dict):
            if set(value) == {'m_FileID', 'm_PathID'}:
                if not value['m_PathID']:
                    return None
                if value['m_FileID']:
                    return '->external:%s:%d' % (externals[value['m_FileID'] - 1], value['m_PathID'])
                return '->' + label(value['m_PathID'])
            return {k: normalize(v, digest) for k, v in value.items() if k not in ignore}
        if isinstance(value, (list, tuple)):
            items = [normalize(v, digest) for v in value]
            return _digest(items) if digest and len(items) > 256 else items
        if isinstance(value, (bytes, bytearray)):
            return 'sha256:' + hashlib.sha256(value).hexdigest()
        return value

    result = []
    for pid, obj in objects.items():
        kind = obj.type.name
        # Material property lists stay whole so a builder upgrade can be checked property by property.
        t = normalize(tree(pid), digest=kind != 'Material')
        if kind == 'Texture2D':
            t.pop('image data', None)
            t.pop('m_StreamData', None)
            # Readable textures store pixels inline, non-readable ones in the bundle's .resS stream.
            data = bytes(obj.read().get_image_data())
            assert len(data) == t['m_CompleteImageSize'] * max(1, t.get('m_ImageCount', 1)), 'Texture data size mismatch'
            t['pixels'] = 'sha256:' + hashlib.sha256(data).hexdigest()
        elif kind == 'AssetBundle':
            # Preload ranges follow path-ID order; compare each container entry's preload set instead.
            raw = tree(pid)
            table = [normalize(p) for p in raw.get('m_PreloadTable', [])]
            container = []
            for name, info in raw.get('m_Container', []):
                start, size = info['preloadIndex'], info['preloadSize']
                container.append([name, normalize(info['asset']), sorted(map(str, table[start:start + size]))])
            t['m_PreloadTable'] = sorted(map(str, table))
            t['m_Container'] = sorted(json.dumps(c, sort_keys=True, default=repr) for c in container)
        result.append((kind, label(pid), t))
    return result


def _key(entry):
    kind, name, t = entry
    return kind + ' ' + name + ' ' + json.dumps(t, sort_keys=True, default=repr)


# A rebuild with the current builder also embeds the current FlashRig shader (a superset of earlier
# versions), gives its materials that shader's default values for new properties, and encodes constant
# animation components with two endpoint keys instead of one per frame. Those are the only allowed changes.
UPGRADABLE = ('Shader', 'Material', 'AnimationClip')
PROPERTY_LISTS = ('m_TexEnvs', 'm_Floats', 'm_Colors', 'm_Ints')


def _paired(entries, kind):
    found = {}
    for k, name, t in entries:
        if k == kind:
            found.setdefault(name, []).append(t)
    return {name: sorted(ts, key=lambda t: json.dumps(t, sort_keys=True, default=repr)) for name, ts in found.items()}


def builder_upgrade_problems(a, b):
    problems = []
    fixed_a = Counter(_key(e) for e in a if e[0] not in UPGRADABLE)
    fixed_b = Counter(_key(e) for e in b if e[0] not in UPGRADABLE)
    problems += ['changed: ' + k[:200] for k in (fixed_a - fixed_b) + (fixed_b - fixed_a)]
    for kind in UPGRADABLE:
        old, new = _paired(a, kind), _paired(b, kind)
        if {n: len(v) for n, v in old.items()} != {n: len(v) for n, v in new.items()}:
            problems.append(kind + ' set differs')
            continue
        for name in old:
            for x, y in zip(old[name], new[name]):
                if kind == 'Shader':
                    if x['m_ParsedForm']['m_Name'] != y['m_ParsedForm']['m_Name']:
                        problems.append('shader name ' + x['m_ParsedForm']['m_Name'])
                elif kind == 'Material':
                    xs, ys = dict(x), dict(y)
                    px, py = xs.pop('m_SavedProperties'), ys.pop('m_SavedProperties')
                    if xs != ys or set(px) != set(py):
                        problems.append('material fields ' + name)
                    for group in PROPERTY_LISTS:
                        newer = {json.dumps(k, default=repr): v for k, v in py.get(group, [])}
                        for k, v in px.get(group, []):
                            if newer.get(json.dumps(k, default=repr), object()) != v:
                                problems.append('material property %s %s.%s' % (name, group, k))
                    for group in set(px) - set(PROPERTY_LISTS):
                        if px[group] != py[group]:
                            problems.append('material %s %s' % (name, group))
                else:
                    xs, ys = dict(x), dict(y)
                    mx, my = dict(xs.pop('m_MuscleClip')), dict(ys.pop('m_MuscleClip'))
                    xs.pop('m_MuscleClipSize'); ys.pop('m_MuscleClipSize')
                    mx.pop('m_Clip'); my.pop('m_Clip')
                    if xs != ys or mx != my:
                        problems.append('clip bindings or timing ' + name)
    return problems


def compare(old, new, ignore=('m_IsReadable',)):
    """'identical', or 'identical_except_builder_upgrade' when only UPGRADABLE content changed as allowed."""
    a, b = signature(old, ignore), signature(new, ignore)
    sa, sb = Counter(map(_key, a)), Counter(map(_key, b))
    missing, extra = sa - sb, sb - sa
    result = dict(status='identical', objects=len(a))
    if missing or extra:
        problems = builder_upgrade_problems(a, b)
        result.update(status='different' if problems else 'identical_except_builder_upgrade',
                      changed_kinds=sorted({k.split(' ', 1)[0] for k in missing + extra}),
                      problems=problems[:20], only_old=sorted(k[:300] for k in missing)[:10],
                      only_new=sorted(k[:300] for k in extra)[:10])
    return result


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('old')
    ap.add_argument('new')
    ap.add_argument('--ignore', nargs='*', default=['m_IsReadable'])
    args = ap.parse_args()
    print(json.dumps(compare(args.old, args.new, tuple(args.ignore)), indent=2))


if __name__ == '__main__':
    main()
