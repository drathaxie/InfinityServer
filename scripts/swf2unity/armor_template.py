"""Extract a native AQW:I armor prefab (ArmorBones_m / ArmorBones_f) as a JSON template: the GameObject tree with
local transforms, SpriteRenderer sorting/colour, CustomizableSlot ids, the other script components by class name,
and the ItemSlots entry order. The Unity armor builder recreates this tree for converted armors, so prefabs match
the client's expectations exactly. Also exports the face kit (head + eye sprites, pivots, head CC mask).

usage: python armor_template.py <native armor bundle> <out dir>
"""
import json
import os
import sys

import UnityPy

bundle, out = sys.argv[1], sys.argv[2]
os.makedirs(out, exist_ok=True)
env = UnityPy.load(bundle)
by_id = {o.path_id: o for o in env.objects}
FACE = {"head", "eyes", "eyescolor", "eyeslight", "eyesblink"}


def comp_objs(go):
    for c in go.m_Component:
        p = c.component if hasattr(c, "component") else c
        o = by_id.get(p.path_id)
        if o:
            yield o


def script_name(obj):
    try:
        return obj.read().m_Script.read().m_ClassName
    except Exception:  # noqa: BLE001
        return "?"


def node(tr_obj, gender, face_out):
    tr = tr_obj.read()
    go = tr.m_GameObject.read()
    n = {"name": go.m_Name, "active": bool(go.m_IsActive),
         "pos": [tr.m_LocalPosition.x, tr.m_LocalPosition.y, tr.m_LocalPosition.z],
         "rot": [tr.m_LocalRotation.x, tr.m_LocalRotation.y, tr.m_LocalRotation.z, tr.m_LocalRotation.w],
         "scale": [tr.m_LocalScale.x, tr.m_LocalScale.y, tr.m_LocalScale.z], "scripts": [], "children": []}
    for o in comp_objs(go):
        t = o.type.name
        if t == "SpriteRenderer":
            d = o.read()
            sp = d.m_Sprite.read() if d.m_Sprite.path_id else None
            n["renderer"] = {"order": d.m_SortingOrder, "layer": d.m_SortingLayerID, "enabled": bool(d.m_Enabled),
                             "color": [d.m_Color.r, d.m_Color.g, d.m_Color.b, d.m_Color.a],
                             "sprite": sp.m_Name if sp else None}
            if sp is not None and go.m_Name.lower() in FACE:
                fname = "%s_%s" % (gender, go.m_Name.lower())
                sp.image.save(os.path.join(out, fname + ".png"))
                face_out.setdefault(go.m_Name.lower(), {}).update(
                    {"png": fname + ".png", "pivot": [sp.m_Pivot.x, sp.m_Pivot.y], "ppu": sp.m_PixelsToUnits})
        elif t == "MonoBehaviour":
            cls = script_name(o)
            entry = {"class": cls}
            if cls in ("CustomizableSlot", "ItemSlots"):
                tree = o.read_typetree()
                if cls == "CustomizableSlot":
                    entry["slot"] = tree["slot"]
                else:
                    slots = []
                    for s in tree["slots"]:
                        spr = by_id.get(s["sprite"]["m_PathID"])
                        msk = by_id.get(s["CCMask"]["m_PathID"])
                        slots.append({"slot": s["slot"], "sprite": spr.read().m_Name if spr else None,
                                      "mask": msk.read().m_Name if msk else None})
                        if s["slot"] == 23 and msk is not None:
                            # Texture names repeat across genders and prefab variants. Resolve the
                            # exact ItemSlots pointer, never the first globally named Head_mask.
                            fn = "%s_head_mask.png" % gender
                            msk.read().image.save(os.path.join(out, fn))
                            face_out.setdefault("head", {})["mask_png"] = fn
                    entry["slots"] = slots
            n["scripts"].append(entry)
        elif t not in ("Transform",):
            n.setdefault("components", []).append(t)
    for ch in tr.m_Children:
        n["children"].append(node(by_id[ch.path_id], gender, face_out))
    return n


result = {}
for o in env.objects:
    if o.type.name != "GameObject":
        continue
    go = o.read()
    if go.m_Name not in ("ArmorBones_m", "ArmorBones_f"):
        continue
    for c in comp_objs(go):
        if c.type.name == "Transform" and not c.read().m_Father.path_id:
            g = go.m_Name[-1]
            if g in result:
                continue
            face = {}
            result[g] = {"tree": node(c, g, face), "face": face}
json.dump(result, open(os.path.join(out, "template.json"), "w"), indent=1)
for g, r in result.items():
    print(g, "face:", sorted(r["face"]), "itemslots:",
          [(e["slot"], e["sprite"], e["mask"]) for s in r["tree"]["scripts"] for e in s.get("slots", [])])
