#!/usr/bin/env python3
"""Push a refreshed data/maps/<map>.json into the `maps` table.

seed_maps() is INSERT-IF-ABSENT (ON CONFLICT(map_id) DO NOTHING), so once a map's row exists,
dropping a re-captured JSON file in data/maps/ and restarting does nothing  the stale DB row
wins. This does the same field mapping as seed.seed_maps() but as an explicit UPDATE, for
refreshing one map after a live-AE re-capture (see e.g. extract_maps.py) without touching any
other map's row.

Usage:
    python capture/update_map_db.py <mapName>          # e.g. graveyard
"""
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "server"))
import db  # noqa: E402

MAPS_DIR = pathlib.Path(__file__).resolve().parent.parent / "data" / "maps"


def update_map(conn, map_name):
    path = MAPS_DIR / f"{map_name}.json"
    doc = json.loads(path.read_text(encoding="utf-8"))
    area = doc["area"]
    meta = {k: v for k, v in area.items() if k not in ("monBranch", "uoBranch")}
    cur = conn.execute(
        "UPDATE maps SET area_name=?, display_name=?, prefab_name=?, soundtrack_id=?, "
        "int_type=?, bundle=?, quest_ids=?, raw=?, doc=? WHERE map_id=?",
        (area.get("areaName"), area.get("DisplayName"), area.get("PrefabName"),
         area.get("SoundtrackID"), area.get("intType"),
         json.dumps(area.get("Bundle"), separators=(",", ":")) if area.get("Bundle") else None,
         json.dumps(area.get("QuestIDs") or [], separators=(",", ":")),
         json.dumps(meta, separators=(",", ":")),
         json.dumps(doc, separators=(",", ":")),
         int(area.get("areaId", 0) or 0)))
    return cur.rowcount


def main():
    if len(sys.argv) != 2:
        print(__doc__)
        raise SystemExit(1)
    map_name = sys.argv[1]
    conn = db.connect()
    try:
        n = update_map(conn, map_name)
        conn.commit()
        print(f"{map_name}: updated maps row(s): {n}")
        if n == 0:
            print("  (no matching map_id  was this map ever seeded? check data/maps/ areaId)")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
