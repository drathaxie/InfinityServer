#!/usr/bin/env python3
"""
Offline test of the authored-placement (pad) layer — no game/socket needed.

Proves the server can author a map's NPC roster: take over a captured map, then
REMOVE an NPC (it disappears from the served monBranch) and ADD one (it appears),
all persisted in SQLite and surfaced through maps.area_payload exactly as the
client would receive it on a map reload.
"""
import pathlib
import tempfile

import db

# Use a throwaway store (temp SQLite file / isolated PG schema) so we never touch live data.
db.use_throwaway()

import seed
import placements
import maps

MAP = "battleon"
ARTIX_PAD = 178   # Artix Krieger (MonID 54) sits on MonMapID/pad 178 in the capture

db.init()
seed.run()        # populate the monsters catalog (montemplates reads it for art)
conn = db.connect()

# maps.py serves docs from the maps table; seed the in-memory cache from the local fixture so
# this offline test exercises the area payload without needing the map seeded into the DB.
import json as _json
_fixture = pathlib.Path(__file__).resolve().parent.parent / "data" / "maps" / f"{MAP}.json"
maps._MAPS[MAP] = _json.loads(_fixture.read_text(encoding="utf-8"))


def mon_ids(branch):
    return sorted(int(b["MonID"]) for b in branch)


def map_ids(branch):
    return sorted(int(b["MonMapID"]) for b in branch)


# 1) A vanilla (un-authored) map serves the captured monBranch untouched.
vanilla = maps.area_payload(MAP, conn)
captured = vanilla["monBranch"]
assert not placements.is_authored(conn, MAP)
assert 54 in mon_ids(captured), "Artix (MonID 54) should be in captured battleon"
print(f"vanilla battleon: {len(captured)} NPCs, authored={placements.is_authored(conn, MAP)}")

# 2) Take over the map: pads seed 1:1 from the captured monBranch and the
#    compiled monBranch round-trips to the same set of monsters.
placements.take_over(conn, MAP)
assert placements.is_authored(conn, MAP)
compiled = placements.compiled_monbranch(conn, MAP)
assert len(compiled) == len(captured), (len(compiled), len(captured))
assert mon_ids(compiled) == mon_ids(captured), "compiled roster must match capture"
assert ARTIX_PAD in map_ids(compiled), "Artix pad/MonMapID preserved through seed"
print(f"taken over: {len(compiled)} pads, roster round-trips ({len(compiled)} NPCs)")

# 3) REMOVE Artix by deleting his pad. He's gone from the served monBranch.
assert placements.pad_delete(conn, MAP, ARTIX_PAD)
after_del = maps.area_payload(MAP, conn)["monBranch"]
assert len(after_del) == len(captured) - 1, (len(after_del), len(captured))
assert ARTIX_PAD not in map_ids(after_del), "Artix pad must be suppressed"
assert 54 not in mon_ids(after_del), "no Artix (MonID 54) after delete"
print(f"deleted Artix pad {ARTIX_PAD}: {len(after_del)} NPCs, MonID 54 present="
      f"{54 in mon_ids(after_del)}")

# 4) ADD a new NPC: new pad + a monster on it. It appears in the served monBranch
#    with a fresh non-colliding MonMapID and resolves art from its MonID template.
new_pad = placements.add_new_pad(
    conn, MAP, '{"X": 5.0, "Y": -7.0, "Frame": "Enter", "Direction": 1}')
assert new_pad >= placements.NEW_PAD_BASE
assert placements.add_mon(conn, MAP, 168, new_pad)   # 168 = Twilly (art already in map)
after_add = maps.area_payload(MAP, conn)["monBranch"]
added = [b for b in after_add if int(b["MonMapID"]) == new_pad]
assert len(added) == 1, "exactly one monster on the new pad"
assert int(added[0]["MonID"]) == 168
assert added[0].get("Bundle"), "added NPC carries its art Bundle (from template)"
assert added[0]["x"] == 5.0 and added[0]["strFrame"] == "Enter"
assert len(after_add) == len(captured)   # -1 (Artix) +1 (new) == same total
print(f"added MonID 168 on new pad {new_pad} @ (5,-7): "
      f"bundle={added[0]['Bundle'].get('Name')}, total now {len(after_add)} NPCs")

# 5) Persistence: a fresh connection sees the same authored roster.
conn2 = db.connect()
reloaded = maps.area_payload(MAP, conn2)["monBranch"]
assert map_ids(reloaded) == map_ids(after_add), "authored roster persists across connections"
print(f"reconnect: same {len(reloaded)} NPCs (persisted to SQLite)")

print("\nALL PLACEMENT TESTS PASSED")
