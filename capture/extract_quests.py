#!/usr/bin/env python3
"""
Mine the quest catalog from the live capture. Every s2c `getQuests` carries a
`quests` dict (questID -> Quest object); we union them across the whole capture,
deduped by QuestID, into data/quests.json for the DB seeder.

A Quest mirrors AQ2D_Server.Game_Engine.Quests.Quest: identity + map/NPC linkage
+ a polymorphic `turnin[]` ($type = Turnin/itemTurnin/interactTurnin/
OpenApopTurnin/WatchCutsceneTurnin) + `Rewards`.
"""
import json, pathlib

CAP = pathlib.Path(r"C:\Program Files (x86)\Steam\steamapps\common"
                   r"\AdventureQuest Worlds Unity Playtest\UserData\Beyond\packets.jsonl")
OUT = pathlib.Path(__file__).resolve().parent.parent / "data" / "quests.json"

quests = {}
for line in CAP.open(encoding="utf-8", errors="replace"):
    if '"getQuests"' not in line:
        continue
    try:
        o = json.loads(line)
    except Exception:
        continue
    if o.get("dir") != "s2c":
        continue
    for qid, q in ((o.get("pkt") or {}).get("quests") or {}).items():
        if isinstance(q, dict) and q.get("QuestID") is not None:
            quests[int(q["QuestID"])] = q          # last write wins (all identical)

OUT.write_text(json.dumps(quests, separators=(",", ":")), encoding="utf-8")
print(f"quests written: {len(quests)} -> {OUT}")
turnins = sum(len(q.get("turnin") or []) for q in quests.values())
print(f"  total turnin rows: {turnins}")
