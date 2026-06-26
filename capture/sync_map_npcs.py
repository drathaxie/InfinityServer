#!/usr/bin/env python3
"""
Sync a map's NPC placements from a live-AE capture — ADD AE's new NPCs (default: battleon).

AreaJoin carries the map's `monBranch` (the NPC/monster placement list). This finds monBranch
entries whose MonMapID is NOT already placed on our map and ADDS them as pads (map_pads +
pad_npcs), pulling each new NPC's catalog from AE (GetMonsterData) so it renders.

MERGE-SAFE / NON-DESTRUCTIVE: it only ADDS pads for MonMapIDs we don't already have. Custom NPCs
(e.g. Tato at pad 99999, which AE's monBranch never contains) and any existing placement are left
untouched. It never deletes. What each NPC SAYS is handled separately by sync_apops.py.

Refresh from AE: remove the infinity markers, play live AE, walk through battleon (the always-on
logger captures the AreaJoin), then run this against the LIVE DB (PG env via the db tunnel).

Usage:
    python capture/sync_map_npcs.py [packets.jsonl] [--map battleon]            # DRY RUN
    python capture/sync_map_npcs.py [packets.jsonl] [--map battleon] --apply    # add new NPCs
"""
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "server"))
import db             # noqa: E402
import placements     # noqa: E402
import montemplates   # noqa: E402

DEFAULT_CAP = pathlib.Path(r"C:\Program Files (x86)\Steam\steamapps\common"
                           r"\AdventureQuest Worlds Unity Playtest\UserData\Beyond\packets.jsonl")


def mine_monbranch(cap_path, mapname):
    """The latest AreaJoin monBranch captured for `mapname` (base map, room stripped)."""
    target = mapname.split("-")[0].lower()
    found = []
    with open(cap_path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if '"AreaJoin"' not in line:
                continue
            try:
                o = json.loads(line)
            except Exception:
                continue
            if o.get("dir") != "s2c":
                continue
            p = o.get("pkt") or {}
            if p.get("Cmd") != "AreaJoin":
                continue
            m = (p.get("strMapName") or p.get("areaName") or "").split("-")[0].lower()
            if m == target and p.get("monBranch"):
                found = p["monBranch"]            # latest capture for this map wins
    return found


def baseline_ids(conn, mapname):
    """The MonMapIDs already placed on the map (authored pads, or the stored captured monBranch)."""
    if placements.is_authored(conn, mapname):
        return {r["pad_id"] for r in conn.execute(
            "SELECT pad_id FROM map_pads WHERE map=?", (mapname,))}
    area = placements._captured_area(mapname) or {}
    return {int(mb.get("MonMapID", 0) or 0) for mb in area.get("monBranch") or []}


def main():
    args = sys.argv[1:]
    apply = "--apply" in args
    mapname = "battleon"
    if "--map" in args:
        i = args.index("--map")
        mapname = args[i + 1] if i + 1 < len(args) else "battleon"
    consumed = {"--map", "--apply", mapname}
    paths = [a for a in args if a not in consumed and not a.startswith("--")]
    cap = pathlib.Path(paths[0]) if paths else DEFAULT_CAP
    mapname = mapname.split("-")[0].lower()

    if not cap.exists():
        print(f"capture not found: {cap}")
        return
    mb_list = mine_monbranch(cap, mapname)
    backend = "Postgres (LIVE)" if db.BACKEND == "postgres" else f"SQLite {db.DB_PATH}"
    print(f"capture: {cap}\n  {mapname}: {len(mb_list)} NPC(s) in the captured monBranch  "
          f"|  target DB: {backend}")
    if not mb_list:
        print(f"\nNo AreaJoin for '{mapname}' in this capture — walk through it on live AE first.")
        return

    conn = db.connect()
    have = baseline_ids(conn, mapname)
    new = [mb for mb in mb_list if int(mb.get("MonMapID", 0) or 0) not in have]

    print(f"\n  already placed: {len(mb_list) - len(new)}")
    print(f"  NEW NPCs:       {len(new)}")
    for mb in new:
        print(f"    +  pad {mb.get('MonMapID')!s:>7}  mon {mb.get('MonID')!s:>6}  "
              f"{mb.get('strMonName', '?'):<22} apop={mb.get('apopID', -1)} "
              f"@({mb.get('x', 0)},{mb.get('y', 0)}) {mb.get('strFrame', 'Enter')}")

    if not apply:
        print("\n(dry run — nothing written. Re-run with --apply to add the new NPCs.)")
        return
    if not new:
        print("\nnothing to add.")
        return

    placements.take_over(conn, mapname)           # ensure authored baseline (no-op if already)
    state = conn.execute("SELECT area_id FROM map_state WHERE map=?", (mapname,)).fetchone()
    area_id = int(state["area_id"]) if state else 0
    # pull catalog for any monsters we don't know yet, so the new NPCs render
    montemplates.resolve_upstream(conn, [int(mb["MonID"]) for mb in new if mb.get("MonID")])
    for mb in new:
        placements.write_pad(conn, mapname, placements._pad_from_monbranch(mb, area_id))
    conn.commit()
    print(f"\napplied: {len(new)} new NPC(s) added to '{mapname}' in {backend}. "
          f"Run sync_apops.py to refresh their dialogue.")


if __name__ == "__main__":
    main()
