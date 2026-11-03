"""Classify armor SWFs by what the swf2unity armor converter can do with them today.

usage: python3 scan_rig.py <classes dir> > rig_survey.tsv

Per SWF (head excluded; the face comes from the native kit):
  static     every body part is one static band (plain conversion, default sprite material)
  animated   some part has animated tracks and every part fits in <= 8 layers
  flipbook   some part needs the not-yet-built flipbook fallback (nested animation or frame-by-frame swaps)
  too_many   some part has more than 8 layers (currently flattened to a static image)
Runs without Pillow/numpy (only the rig maths is needed).
"""
import os
import sys
import types

for mod in ("PIL", "PIL.Image"):  # render.py imports Pillow at module level; the rig walk never uses it
    sys.modules.setdefault(mod, types.ModuleType(mod))
sys.modules["PIL"].Image = sys.modules["PIL.Image"]
sys.modules.setdefault("ccscripts", types.ModuleType("ccscripts"))
sys.modules["ccscripts"].ffdec = None
sys.modules["ccscripts"].shade_offsets = None

import rig  # noqa: E402
import swfparse as sp  # noqa: E402

PARTS = ("Chest", "Hip", "Shoulder", "Hand", "Thigh", "Shin", "FootIdle", "Foot", "RobeBack", "Robe")


def classify(path):
    m = sp.load(path)
    r = rig.Rig(m)
    status, max_layers, animated_parts = "static", 0, []
    for cid, name in m.symbols.items():
        if "." in name:
            continue
        part = next((p for p in PARTS if name.endswith(p) and name[-len(p) - 1:-len(p)] in "MF"), None)
        if part is None:
            continue
        layers = r.decompose(cid)
        max_layers = max(max_layers, len(layers))
        kinds = {l.kind for l in layers}
        if "flipbook" in kinds:
            status = "flipbook"
        elif len(layers) > 8 and status != "flipbook":
            status = "too_many"
        elif kinds & {"track"} and status == "static":
            status = "animated"
        if kinds & {"track", "flipbook"}:
            animated_parts.append(part)
    return status, max_layers, animated_parts, len(r.notes)


def main():
    root = sys.argv[1]
    print("file\tstatus\tmax_layers\tanimated_parts\tnotes")
    for f in sorted(os.listdir(root)):
        if not f.lower().endswith(".swf"):
            continue
        try:
            s, ml, ap, n = classify(os.path.join(root, f))
            print("%s\t%s\t%d\t%s\t%d" % (f, s, ml, ",".join(sorted(set(ap))), n), flush=True)
        except Exception as e:  # noqa: BLE001
            print("%s\tERR\t0\t%s\t0" % (f, type(e).__name__), flush=True)


if __name__ == "__main__":
    main()
