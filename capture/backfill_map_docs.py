#!/usr/bin/env python3
r"""
Synthesize a minimal, joinable AreaJoin doc for every placeholder map row that has catalog
Bundle metadata but no real captured doc (import_remaining_bundles.py only wrote id/name/bundle
for these — maps.area_payload() needs a full {"area":..., "cells":{...}} doc to serve anything,
and returns None without one, silently bouncing the player to Infinity Portal).

The only two fields the client actually needs to load geometry are Bundle (which .unity3d to
fetch) and PrefabName (which prefab inside it to instantiate) — confirmed against every real
captured map doc in data/maps/*.json, e.g. farm.json: Bundle.Filename "11_map-farm.unity3d" ->
PrefabName "map-farm". monBranch/uoBranch/QuestIDs can validly be empty: an empty room, no
monsters, no quests — exactly like cell_payload() already synthesizes a minimal CellJoin when no
capture exists for a frame (see maps.py). This gives the same graceful-degradation treatment to
AreaJoin that CellJoin already has.

Filter: only bundles whose derived PrefabName starts with "map-" (case-insensitive) are real
top-level zone maps — verified against the full Type=="MAP" catalog: cinematic/background/prop
bundles that got miscategorized as MAP (e.g. "BGs-Bludrut2", "IceElementalDead",
"..._weaponGO") never follow this convention, so they're skipped rather than turned into bogus
"joinable" areas.

str_map_name (the join lookup key) is derived from the PrefabName minus its "map-" prefix
(e.g. "map-pineoak" -> "pineoak") — this is what our OWN server treats as the join key, not an
attempt to reproduce AE's internal routing string (which occasionally has an unpredictable
prefix quirk we can't know without a real capture, e.g. orcpath/orctown -> horcpath/horctown).

Usage:
    python capture/backfill_map_docs.py
"""
import json
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "server"))
import db  # noqa: E402

PREFAB_RE = re.compile(r"^map-", re.IGNORECASE)


def prefab_from_filename(fn):
    stem = re.sub(r"\.unity3d$", "", fn.rsplit("/", 1)[-1], flags=re.IGNORECASE)
    return re.sub(r"^\d+_", "", stem)


def main():
    db.init()
    conn = db.connect()
    rows = conn.execute(
        "SELECT map_id, display_name, bundle FROM maps WHERE doc IS NULL AND bundle IS NOT NULL"
    ).fetchall()

    updated = 0
    skipped_not_map = 0
    for r in rows:
        try:
            bundle = json.loads(r["bundle"] or "{}")
        except (TypeError, ValueError):
            continue
        fn = bundle.get("Filename") or ""
        if not fn:
            continue
        prefab = prefab_from_filename(fn)
        if not PREFAB_RE.match(prefab):
            skipped_not_map += 1
            continue
        str_map_name = PREFAB_RE.sub("", prefab).lower()
        display_name = r["display_name"] or str_map_name
        area = {
            "Cmd": "AreaJoin",
            "areaName": f"{str_map_name}-1",
            "strMapName": str_map_name,
            "DisplayName": display_name,
            "Bundle": bundle,
            "PrefabName": prefab,
            "intType": 1,
            "sExtra": "",
            "areaId": r["map_id"],
            "Frame": "Enter",
            "Pad": "Spawn",
            "monBranch": [],
            "uoBranch": [],
            "QuestIDs": [],
        }
        doc = {"area": area, "cells": {}}
        conn.execute(
            "UPDATE maps SET str_map_name=?, prefab_name=?, doc=? WHERE map_id=?",
            (str_map_name, prefab, json.dumps(doc, separators=(",", ":")), r["map_id"]),
        )
        updated += 1
    conn.commit()
    print(f"{len(rows)} doc-less bundle-backed map rows scanned")
    print(f"synthesized {updated} minimal joinable AreaJoins (empty rooms, no monsters/quests)")
    print(f"skipped {skipped_not_map} non-map bundles (cutscene/background/prop assets miscategorized as MAP)")


if __name__ == "__main__":
    main()
