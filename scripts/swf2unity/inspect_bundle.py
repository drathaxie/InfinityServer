"""Dump a native AQW:I bundle's prefab structure: hierarchy, components, MonoBehaviour script classes and their
serialized fields, SpriteRenderer sprites/colours/sorting, materials and shaders. Used to learn the armor contract.

usage: python inspect_bundle.py <bundle.unity3d> [max_depth]
"""
import collections
import sys

import UnityPy

env = UnityPy.load(sys.argv[1])
max_depth = int(sys.argv[2]) if len(sys.argv) > 2 else 12
types = collections.Counter(o.type.name for o in env.objects)
print("object types:", dict(types))
by_id = {o.path_id: o for o in env.objects}


def script_name(mb):
    try:
        s = mb.m_Script.read()
        return "%s.%s (%s)" % (s.m_Namespace, s.m_ClassName, s.m_AssemblyName)
    except Exception as e:  # noqa: BLE001
        return "?script %s" % e


def describe_component(ptr):
    try:
        obj = ptr.deref() if hasattr(ptr, "deref") else by_id[ptr.path_id]
        t = obj.type.name
        d = obj.read()
    except Exception as e:  # noqa: BLE001
        return "?%s" % e
    if t == "SpriteRenderer":
        sp = d.m_Sprite.read().m_Name if d.m_Sprite.path_id else None
        mats = []
        for m in d.m_Materials:
            if m.path_id:
                try:
                    md = m.read()
                    try:
                        sh = md.m_Shader.read().m_Name if md.m_Shader.path_id else None
                    except Exception:  # noqa: BLE001 - built-in shader lives in unity_builtin_extra
                        sh = "builtin:%d" % md.m_Shader.path_id
                    mats.append("%s[%s]" % (md.m_Name, sh))
                except Exception:  # noqa: BLE001
                    mats.append("builtin-material:%d" % m.path_id)
        c = d.m_Color
        return "SpriteRenderer(sprite=%s, order=%s, color=(%.2f,%.2f,%.2f,%.2f), mats=%s)" % (
            sp, d.m_SortingOrder, c.r, c.g, c.b, c.a, mats)
    if t == "MonoBehaviour":
        tree = obj.read_typetree() if hasattr(obj, "read_typetree") else {}
        keys = {k: (v if not isinstance(v, (list, dict)) or len(str(v)) < 160 else str(v)[:160] + "...")
                for k, v in tree.items() if not k.startswith("m_") or k in ("m_Name",)}
        return "MonoBehaviour %s %s" % (script_name(d), keys)
    if t == "Transform":
        return None
    return t


def walk(go_obj, depth):
    go = go_obj.read()
    comps = [describe_component(c.component if hasattr(c, "component") else c) for c in go.m_Component]
    comps = [c for c in comps if c]
    print("  " * depth + "- %s %s" % (go.m_Name, comps))
    if depth >= max_depth:
        return
    tr = None
    for c in go.m_Component:
        p = c.component if hasattr(c, "component") else c
        o = by_id.get(p.path_id)
        if o and o.type.name in ("Transform", "RectTransform"):
            tr = o.read()
    if tr is None:
        return
    for child in tr.m_Children:
        ct = by_id[child.path_id].read()
        walk(by_id[ct.m_GameObject.path_id], depth + 1)


roots = []
for o in env.objects:
    if o.type.name == "GameObject":
        go = o.read()
        for c in go.m_Component:
            p = c.component if hasattr(c, "component") else c
            t = by_id.get(p.path_id)
            if t and t.type.name == "Transform" and not t.read().m_Father.path_id:
                roots.append(o)
for r in roots:
    print("ROOT", r.read().m_Name)
    walk(r, 0)
