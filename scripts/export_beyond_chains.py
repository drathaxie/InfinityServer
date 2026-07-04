#!/usr/bin/env python3
"""
Seed Beyond's bootstrap quest chains from OUR database.

Beyond (the bot launcher) runs against live AQW too, so its chains.json stays the
source of truth the user can hand-author for new content; this script just fills
it with VERIFIED data for the quest lines our server carries — per quest the
right area/frame (from the served monster placements) and the target monster
names (per-objective kill-credit resolution from server/questdb.py). Monster
NAMES, not ids, so the same entry works on live AQW where the client never sees
RefIDs.

Usage (from the repo root, after the local DB is seeded):
    python scripts/export_beyond_chains.py [path\\to\\Infinity-Beyond]

Writes one Library script per chain to Library/Scripts/<Name>.json in the
Beyond repo — the same format the launcher's Library window loads (and the
staging copies to PR into the community Infinity-Files repo, which is where
"Update Library" downloads from). The chains are distributed via the Library,
NOT baked into the mod's embedded bootstrap, so Data/chains.json is left alone.
"""
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "server"))

import db      # noqa: E402
import questdb # noqa: E402

# Chain name -> first quest id. The rest of each chain follows prevQuest links
# (quest N+1 is the quest whose prevQuest == N), same ordering the client's
# storylineData walks.
CHAINS = {
    "Lair": 19,
    "Bludrut Keep": 157,
    "Zard Killer": 185,
    "Forest Zards": 236,
}

DEFAULT_BEYOND = pathlib.Path(__file__).resolve().parents[2] / "Infinity-Beyond"


def _chain_qids(quests, first_qid):
    """Follow prevQuest links forward from first_qid until no quest continues it."""
    out, cur = [], first_qid
    while cur is not None and str(cur) in quests:
        out.append(cur)
        cur = next((q["id"] for q in quests.values() if q["prevQuest"] == cur), None)
        if cur in out:      # defensive: a prevQuest cycle would loop forever
            break
    return out


def _entry(kb, qid):
    q = kb["quests"][str(qid)]
    # Hunt objectives (kill / item-collect) target monsters by CATALOG ID — the
    # bot matches Monster.ID (client-visible on both our server and live AE), and
    # ids are unambiguous where the same MonID carries different display names
    # across cells (bludrut's 190 is "Skeletal Warrior" in one room, "Undead
    # Warrior" in another). The _note carries the human name for the editor. The
    # entry frame is just a starting cell — the runner is map-aware and re-routes
    # to whatever cell actually holds the target.
    hunt = [o for o in q["objectives"] if o.get("locations")]
    entry = {"qid": qid, "area": q["map"], "frame": q["frame"] or "",
             "pad": q["pad"] or "Spawn", "items": 1}
    mons, names = [], []
    if hunt:
        spot = max(hunt[0]["locations"],
                   key=lambda l: l["count"] + (1000 if l["map"] == q["map"] else 0))
        entry["area"], entry["frame"] = spot["map"], spot["frame"]
        entry["pad"] = "Spawn"
        for o in hunt:
            for mid in o.get("monsters") or [m["monId"] for m in o.get("sources", [])]:
                if mid not in mons:
                    mons.append(mid)
                    nm = (kb["monsters"].get(str(mid)) or {}).get("name")
                    if nm:
                        names.append(f"{mid}={nm}")
    if mons:
        entry["mon"] = [str(m) for m in mons]
    note = q["name"] + ("" if q["huntable"] else " (has non-hunt objectives)")
    if names:
        note += "  [" + ", ".join(names) + "]"
    entry["_note"] = note
    return entry


def main():
    beyond = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_BEYOND
    scripts_dir = beyond / "Library" / "Scripts"
    if not beyond.exists():
        sys.exit(f"Beyond repo not found at {beyond} — pass the repo path")
    scripts_dir.mkdir(parents=True, exist_ok=True)

    db.init()
    conn = db.connect()
    kb = questdb.build(conn)
    conn.close()

    for name, first in CHAINS.items():
        qids = _chain_qids(kb["quests"], first)
        script = {name: [_entry(kb, qid) for qid in qids]}
        path = scripts_dir / (name.replace(" ", "") + ".json")
        path.write_text(json.dumps(script, indent=2) + "\n", encoding="utf-8")
        huntable = sum(1 for q in qids if kb["quests"][str(q)]["huntable"])
        print(f"{name}: {len(qids)} quests ({huntable} fully huntable) -> {path.name}")

    print(f"wrote {len(CHAINS)} Library script(s) to {scripts_dir}")
    print("PR these into the Infinity-Files repo's Scripts/ folder so "
          "\"Update Library\" serves them to everyone.")


if __name__ == "__main__":
    main()
