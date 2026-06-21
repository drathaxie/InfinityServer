#!/usr/bin/env python3
"""
Mine the Apop catalog from the capture. s2c `getApop` carries `apopData`
(apopID -> a JSON *string* of the Apop object: panels/elements/buttons/actors,
the AQ2D Apop.cs dialog+menu+cutscene system). We union them across the capture,
parsing the inner JSON, into data/apops.json (+ a pretty per-apop dump).
"""
import json, pathlib

CAP = pathlib.Path(r"C:\Program Files (x86)\Steam\steamapps\common"
                   r"\AdventureQuest Worlds Unity Playtest\UserData\Beyond\packets.jsonl")
OUT = pathlib.Path(__file__).resolve().parent.parent / "data" / "apops.json"
PRETTY = OUT.parent / "apops"
PRETTY.mkdir(parents=True, exist_ok=True)

apops = {}
for line in CAP.open(encoding="utf-8", errors="replace"):
    if '"getApop"' not in line:
        continue
    try:
        o = json.loads(line)
    except Exception:
        continue
    if o.get("dir") != "s2c":
        continue
    for aid, s in ((o.get("pkt") or {}).get("apopData") or {}).items():
        try:
            apops[int(aid)] = json.loads(s) if isinstance(s, str) else s
        except Exception:
            pass

OUT.write_text(json.dumps(apops, separators=(",", ":")), encoding="utf-8")
for aid, a in apops.items():
    name = (a.get("name") or f"apop{aid}").replace("/", "_")
    (PRETTY / f"{aid}_{name}.json").write_text(json.dumps(a, indent=2), encoding="utf-8")

print(f"apops written: {len(apops)} -> {OUT}")
for aid, a in sorted(apops.items()):
    actors = ", ".join(x.get("name", "?") for x in (a.get("actors") or []))
    print(f"  apop {aid:>4}  name={a.get('name'):<22} panels={len(a.get('panels') or [])}"
          f"  actors=[{actors}]")
