#!/usr/bin/env python3
"""Harvest AE's public GetMonsterData catalog without inventing monster records.

Writes one canonical JSON file per returned MonID under data/monsters and a
dated/full snapshot under capture/harvest. Existing files are only rewritten
when --apply is supplied; the default mode is a report-only diff.
"""
import argparse
import datetime as dt
import json
import pathlib
import time

import requests

ROOT = pathlib.Path(__file__).resolve().parent.parent
MONSTERS = ROOT / "data" / "monsters"
OUT = ROOT / "capture" / "harvest"
URL = "https://infinity.aq.com/game/api/data/GetMonsterData?ids="


def existing_catalog():
    out = {}
    for path in MONSTERS.glob("*.json"):
        if path.name == "index.json":
            continue
        try:
            row = json.loads(path.read_text(encoding="utf-8"))
            out[int(row["ID"])] = row
        except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
            continue
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--max-id", type=int, default=5000)
    ap.add_argument("--batch", type=int, default=200)
    ap.add_argument("--delay", type=float, default=0.1)
    ap.add_argument("--apply-missing", action="store_true",
                    help="write only new MonIDs; preserve existing/customized definitions")
    ap.add_argument("--refresh-existing", action="store_true",
                    help="also replace changed existing definitions with AE's current rows")
    args = ap.parse_args()

    found = {}
    session = requests.Session()
    session.headers["User-Agent"] = "InfinityServer catalog reconciler"
    for start in range(1, args.max_id + 1, args.batch):
        ids = range(start, min(start + args.batch, args.max_id + 1))
        response = session.get(URL + ",".join(map(str, ids)), timeout=30)
        response.raise_for_status()
        rows = response.json()
        for row in rows if isinstance(rows, list) else []:
            if isinstance(row, dict) and row.get("ID") is not None:
                found[int(row["ID"])] = row
        print(f"ids {start:>5}-{min(start + args.batch - 1, args.max_id):<5} "
              f"total={len(found)}")
        time.sleep(args.delay)

    catalog = [found[mid] for mid in sorted(found)]
    OUT.mkdir(parents=True, exist_ok=True)
    snapshot = OUT / f"monsters_live_{dt.date.today():%Y%m%d}.json"
    snapshot.write_text(json.dumps(catalog, indent=2), encoding="utf-8")

    old = existing_catalog()
    added = sorted(set(found) - set(old))
    changed = sorted(mid for mid in set(found) & set(old) if found[mid] != old[mid])
    absent = sorted(set(old) - set(found))
    print(f"snapshot={snapshot} live={len(found)} source={len(old)} "
          f"added={len(added)} changed={len(changed)} source_only={len(absent)}")
    if added:
        print("added:", ",".join(map(str, added)))
    if changed:
        print("changed:", ",".join(map(str, changed)))

    if args.apply_missing or args.refresh_existing:
        MONSTERS.mkdir(parents=True, exist_ok=True)
        write_ids = added + (changed if args.refresh_existing else [])
        for mid in write_ids:
            (MONSTERS / f"{mid}.json").write_text(
                json.dumps(found[mid], indent=2), encoding="utf-8")
        print(f"applied={len(write_ids)} (no source-only records removed)")


if __name__ == "__main__":
    main()
