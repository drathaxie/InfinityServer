#!/usr/bin/env python3
"""
Mine per-map world data from the live capture so the server can serve the map
the client actually requests (instead of replaying one area forever).

CellJoin packets aren't labelled with their map, so we walk the capture in
order and bucket each CellJoin under the most-recent AreaJoin's map. Output:

  data/maps/<mapName>.json = {
     "area":  <one AreaJoin payload for that map>,
     "cells": { "<Frame>": <CellJoin payload>, ... }
  }
"""
import json, pathlib

CAP = pathlib.Path(r"C:\Program Files (x86)\Steam\steamapps\common"
                   r"\AdventureQuest Worlds Unity Playtest\UserData\Beyond\packets.jsonl")
OUT = pathlib.Path(__file__).resolve().parent.parent / "data" / "maps"
OUT.mkdir(parents=True, exist_ok=True)

maps = {}            # mapName -> {"area": pkt, "cells": {frame: pkt}}
cur = None

for line in CAP.open(encoding="utf-8", errors="replace"):
    try:
        o = json.loads(line)
    except Exception:
        continue
    if o.get("dir") != "s2c":
        continue
    p = o.get("pkt") or {}
    cmd = p.get("Cmd")
    if cmd == "AreaJoin":
        m = p.get("strMapName") or p.get("areaName")
        if not m:
            continue
        cur = m
        maps.setdefault(m, {"area": None, "cells": {}})
        if maps[m]["area"] is None:
            maps[m]["area"] = p           # first area instance for this map
    elif cmd == "CellJoin" and cur:
        frame = p.get("Frame") or "Enter"
        maps[cur]["cells"].setdefault(frame, p)

written = 0
for m, d in maps.items():
    if d["area"] is None:
        continue
    safe = "".join(ch if ch.isalnum() else "_" for ch in m)
    (OUT / f"{safe}.json").write_text(json.dumps(d), encoding="utf-8")
    written += 1

print(f"maps written: {written}")
for m, d in sorted(maps.items(), key=lambda kv: -len(kv[1]["cells"])):
    if d["area"]:
        print(f"  {m:<18} cells={len(d['cells'])}  ({', '.join(list(d['cells'])[:8])})")
