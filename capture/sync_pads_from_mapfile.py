#!/usr/bin/env python3
"""
Add a map's missing NPC pads to the DB from its stored map file — ADD only, never remove.

Once a map has been "taken over" in the editor it is AUTHORED: area_payload() serves the pad
layer (map_pads + pad_npcs) and ignores the captured monBranch in data/maps/<map>.json. So
importing a release into the map file is not enough for such a map — battleon is authored, which
is why a newly added NPC can sit in the map file and still not appear in game.

This reconciles the two: every monBranch entry in the map file whose MonMapID has no pad becomes
one. It is the same write path as capture/sync_map_npcs.py, but sourced from the stored map file
rather than from "the latest AreaJoin in a capture". That matters because a capture holds several
joins of a map and the newest is not necessarily the richest — with the Infinity markers
installed it may even be one of OUR server's, which does not contain AE's new NPC at all.
capture/import_release.py is what puts the union of every join into the map file.

Usage:
    python capture/sync_pads_from_mapfile.py [--map battleon]              # DRY RUN
    python capture/sync_pads_from_mapfile.py [--map battleon] --apply
"""
import argparse
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "server"))
import db             # noqa: E402
import placements     # noqa: E402

MAPS_DIR = ROOT / "data" / "maps"


def _safe(name):
    return "".join(ch if ch.isalnum() else "_" for ch in name)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--map", default="battleon")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--pad", action="append", type=int, default=[], metavar="PAD_ID",
                    help="add only these pads. Use it when the map file carries an AE placement "
                         "of an NPC we already placed ourselves somewhere else — adding both "
                         "would put two of that NPC in the map.")
    args = ap.parse_args()
    mapname = args.map.split("-")[0].lower()

    path = MAPS_DIR / f"{_safe(mapname)}.json"
    if not path.exists():
        print(f"no map file: {path}")
        return 1
    area = (json.loads(path.read_text(encoding="utf-8")) or {}).get("area") or {}
    mb_list = area.get("monBranch") or []

    conn = db.connect()
    authored = placements.is_authored(conn, mapname)
    have = {int(r["pad_id"]) for r in conn.execute(
        "SELECT pad_id FROM map_pads WHERE map=?", (mapname,)).fetchall()}

    print(f"map file: {path.relative_to(ROOT)}  ({len(mb_list)} placements)")
    print(f"  backend={db.BACKEND}  authored={authored}  pads already in DB={len(have)}")
    if not authored:
        print("  NOTE: this map is not authored, so area_payload() already serves the map file "
              "directly. Adding pads here would switch it to the authored layer — only do that "
              "deliberately.")

    new = [mb for mb in mb_list if int(mb.get("MonMapID", 0) or 0) not in have]
    if args.pad:
        wanted = set(args.pad)
        held = [mb for mb in new if int(mb.get("MonMapID", 0) or 0) not in wanted]
        new = [mb for mb in new if int(mb.get("MonMapID", 0) or 0) in wanted]
        for mb in held:
            print(f"    -  pad {mb.get('MonMapID')} {mb.get('strMonName', '?')!r} "
                  f"held back (not in --pad)")
    print(f"  missing pads: {len(new)}")
    for mb in new:
        print(f"    +  pad {mb.get('MonMapID')!s:>7}  mon {mb.get('MonID')!s:>6}  "
              f"{mb.get('strMonName', '?'):<22} apop={mb.get('apopID', -1)} "
              f"@({mb.get('x', 0)},{mb.get('y', 0)}) {mb.get('strFrame', 'Enter')}")

    if not args.apply:
        print("\nDRY RUN — nothing written. Re-run with --apply.")
        return 0
    if not new:
        print("\nnothing to add.")
        return 0

    placements.take_over(conn, mapname)      # no-op when already authored
    state = conn.execute("SELECT area_id FROM map_state WHERE map=?", (mapname,)).fetchone()
    area_id = int(state["area_id"]) if state else 0
    for mb in new:
        placements.write_pad(conn, mapname, placements._pad_from_monbranch(mb, area_id))
    conn.commit()
    print(f"\napplied: {len(new)} pad(s) added to '{mapname}'.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
