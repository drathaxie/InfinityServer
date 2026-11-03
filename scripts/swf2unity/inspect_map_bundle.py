"""Read-only native map evidence: cells, transforms, colliders and transitions.

Usage: python scripts/swf2unity/inspect_map_bundle.py MAP.unity3d --out REPORT.json
Serialized bounds describe authored geometry, not proof of runtime navigation.
"""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path

import numpy as np
import UnityPy


def trs(tree):
    p, q, s = (tree[key] for key in ('m_LocalPosition', 'm_LocalRotation', 'm_LocalScale'))
    x, y, z, w = (q[key] for key in ('x', 'y', 'z', 'w'))
    result = np.eye(4)
    result[:3, :3] = np.array([
        [1 - 2*y*y - 2*z*z, 2*x*y - 2*z*w, 2*x*z + 2*y*w],
        [2*x*y + 2*z*w, 1 - 2*x*x - 2*z*z, 2*y*z - 2*x*w],
        [2*x*z - 2*y*w, 2*y*z + 2*x*w, 1 - 2*x*x - 2*y*y],
    ]) @ np.diag([s[key] for key in ('x', 'y', 'z')])
    result[:3, 3] = [p[key] for key in ('x', 'y', 'z')]
    return result


def inspect(path):
    env = UnityPy.load(str(path))
    readers = list(env.objects)
    objects = {obj.path_id: obj for obj in readers}
    if len(objects) != len(readers):
        raise ValueError('Bundle contains duplicate path IDs across serialized files; file-scoped resolution is required')
    gos, transforms, scripts, failures = {}, {}, {}, []
    for pid, obj in objects.items():
        try:
            if obj.type.name == 'GameObject':
                gos[pid] = obj.read_typetree()
            elif obj.type.name in ('Transform', 'RectTransform'):
                transforms[pid] = obj.read_typetree()
            elif obj.type.name == 'MonoScript':
                scripts[pid] = obj.read_typetree()
        except Exception as exc:
            failures.append({'path_id': pid, 'type': obj.type.name, 'error': str(exc)})
    go_transforms = {tree['m_GameObject']['m_PathID']: pid for pid, tree in transforms.items()}
    worlds, paths = {}, {}

    def world(pid):
        if pid not in worlds:
            tree = transforms[pid]
            parent = tree['m_Father']['m_PathID']
            worlds[pid] = (world(parent) if parent else np.eye(4)) @ trs(tree)
        return worlds[pid]

    def go_path(gid):
        if gid not in paths:
            transform = transforms[go_transforms[gid]]
            parent = transform['m_Father']['m_PathID']
            prefix = go_path(transforms[parent]['m_GameObject']['m_PathID']) + '/' if parent else ''
            paths[gid] = prefix + gos[gid]['m_Name']
        return paths[gid]

    nodes = []
    for gid, go in gos.items():
        tid = go_transforms[gid]
        transform = transforms[tid]
        matrix = world(tid)
        node = {'path': go_path(gid), 'path_id': gid, 'active_self': bool(go['m_IsActive']),
                'layer': go['m_Layer'], 'local_position': transform['m_LocalPosition'],
                'local_scale': transform['m_LocalScale'], 'world_position': matrix[:3, 3].tolist(),
                'components': []}
        for entry in go['m_Component']:
            pid = entry['component']['m_PathID']
            obj = objects[pid]
            kind = obj.type.name
            if kind in ('Transform', 'RectTransform'):
                continue
            try:
                tree = obj.read_typetree()
                component = {'type': kind, 'path_id': pid}
                if kind == 'MonoBehaviour':
                    script = scripts.get(tree['m_Script']['m_PathID'], {})
                    component.update(script=script.get('m_ClassName', '?'),
                                     namespace=script.get('m_Namespace'),
                                     assembly=script.get('m_AssemblyName'),
                                     fields={key: value for key, value in tree.items() if not key.startswith('m_')})
                elif 'Collider' in kind:
                    component['fields'] = tree
                    if 'm_Size' in tree:
                        center = tree.get('m_Center', tree.get('m_Offset', {}))
                        size = tree['m_Size']
                        points = []
                        for xx in (-0.5, 0.5):
                            for yy in (-0.5, 0.5):
                                pt = matrix @ np.array([center.get('x', 0) + xx*size['x'],
                                                        center.get('y', 0) + yy*size['y'],
                                                        center.get('z', 0), 1])
                                points.append(pt[:2])
                        points = np.array(points)
                        component['world_aabb_xy'] = [points.min(axis=0).tolist(), points.max(axis=0).tolist()]
                elif kind == 'SpriteRenderer':
                    sid = tree['m_Sprite']['m_PathID']
                    sprite = objects[sid].read_typetree() if sid in objects else {}
                    component.update(sprite=sprite.get('m_Name'), rect=sprite.get('m_Rect'),
                                     pixels_per_unit=sprite.get('m_PixelsToUnits'),
                                     sorting_order=tree.get('m_SortingOrder'),
                                     sorting_layer=tree.get('m_SortingLayerID'))
                else:
                    continue
                node['components'].append(component)
            except Exception as exc:
                failures.append({'path_id': pid, 'type': kind, 'error': str(exc)})
        nodes.append(node)
    nodes.sort(key=lambda node: node['path'])
    cells = [node for node in nodes if any(c.get('script') == 'MapCell' for c in node['components'])]
    summaries = []
    for cell in cells:
        members = [node for node in nodes if node['path'] == cell['path'] or node['path'].startswith(cell['path'] + '/')]
        grouped = {}
        for node in members:
            for component in node['components']:
                key = component.get('script', component['type'])
                grouped.setdefault(key, []).append({'path': node['path'], 'world_position': node['world_position'],
                                                     **component})
        summaries.append({'path': cell['path'], 'active_self': cell['active_self'],
                          'settings': grouped.get('MapCell', []),
                          'components': grouped})
    return {'schema': 'infinity-native-map-inspection-v1', 'source': str(path.resolve()),
            'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
            'object_counts': dict(Counter(obj.type.name for obj in readers)),
            'cells': summaries, 'nodes': nodes, 'read_failures': failures,
            'proof_boundary': 'Serialized local bundle only; no runtime or latest-version claim.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('bundle', type=Path)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    report = inspect(args.bundle)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2), encoding='utf-8')
    for cell in report['cells']:
        parts = cell['components']
        print(cell['path'], 'components:', {key: len(value) for key, value in parts.items()})
        for collider in parts.get('BoxCollider', []):
            print('  camera/collider', collider['path'], collider.get('world_aabb_xy'))
        for transition in parts.get('MapGoToCell', []) + parts.get('MapChangeArea', []):
            print('  transition', transition['path'], transition['world_position'], transition['fields'])
    print('Read failures:', len(report['read_failures']))
    print('Report:', args.out)


if __name__ == '__main__':
    main()
