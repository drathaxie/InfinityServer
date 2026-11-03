#!/usr/bin/env python3
"""
Pull a live-AE content release out of a packet capture — ADD what we don't have yet.

A release ships as maps, the monsters placed on them, and the dialogue those NPCs speak. Walking
the new content in live AE puts all three in the capture:

  AreaJoin  -> the map (its bundle, areaId, QuestIDs) and its monBranch placements
  CellJoin  -> each cell of that map (bucketed under the most recent AreaJoin)
  getApop   -> what the NPCs on it say
  getQuests -> the quests they give, and the item definitions of what they reward

This writes data/maps/<map>.json for maps we have no file for, and merges new apops and quests
into data/apops.json and data/quests.json. The monster catalog needs no separate step:
montemplates.file_catalog() reads every data/maps/*.json monBranch, so a new map's monsters are
seeded with it.

STRICTLY ADDITIVE, unlike capture/extract_maps.py and capture/extract_quests.py, which rewrite
their whole output from whatever this one capture happened to contain. That matters because a
capture is one player's session: it sees the cells they walked and the quests they opened and
nothing else, so a wholesale rewrite would drop content we already had. Use --force-map to
rewrite one map deliberately.

For a map we already have, --merge-placements adds only the monBranch entries whose MonMapID is
missing, leaving its cells and every existing placement alone. That is how a new NPC standing in
an old town (Aranx in battleon) gets picked up without touching the rest of the map.

Usage:
    python capture/import_release.py [packets.jsonl]                 # DRY RUN
    python capture/import_release.py [packets.jsonl] --apply
    python capture/import_release.py [packets.jsonl] --apply --force-map aranx
    python capture/import_release.py [packets.jsonl] --apply --merge-placements battleon
"""
import argparse
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
MAPS_DIR = ROOT / "data" / "maps"
APOPS_FILE = ROOT / "data" / "apops.json"
QUESTS_FILE = ROOT / "data" / "quests.json"
ITEMS_FILE = ROOT / "data" / "items.json"
DEFAULT_CAP = pathlib.Path(
    r"C:\Program Files (x86)\Steam\steamapps\common"
    r"\AdventureQuest Worlds Unity Playtest\UserData\Beyond\packets.jsonl")


# Our own custom NPCs live in a synthetic pad range AE's monBranch never uses (Tato at 99999,
# Redux at 99998, the Vinchi statue placement at 100000). A capture taken with the Infinity
# markers still installed contains OUR AreaJoins as well as AE's, so those pads show up as
# "new" against a map file that never carried them. They are already placed by our own seeding,
# so importing them here would be importing our own content back in as if it were AE's.
CUSTOM_PAD_FLOOR = 99998


def _safe(name):
    """The filename data/maps uses for a map (extract_maps.py's convention)."""
    return "".join(ch if ch.isalnum() else "_" for ch in name)


def merge_placements(map_name, captured_placements, target):
    """-> (doc, added, skipped_custom). Adds monBranch entries we have no pad for."""
    doc = json.loads(target.read_text(encoding="utf-8"))
    area = doc.get("area") or {}
    have = {int(e["MonMapID"]) for e in (area.get("monBranch") or [])
            if e.get("MonMapID") is not None}
    added, skipped_custom = [], []
    for mmid in sorted(captured_placements):
        e = captured_placements[mmid]
        if mmid in have:
            continue
        if mmid >= CUSTOM_PAD_FLOOR:
            skipped_custom.append(e)
            continue
        added.append(e)
    if added:
        area.setdefault("monBranch", []).extend(added)
        doc["area"] = area
    return doc, added, skipped_custom


# Per-instance fields that ride an inventory/reward row but are not part of the catalog
# definition. Storing them would bake one player's copy of the item into the shared catalog.
_INSTANCE_FIELDS = ("CharItemID", "LootID", "Quantity", "Equipped", "Banked", "purchaseDate",
                    "ItemPattern", "enhCount", "ProcID", "Meta", "CharPatternID", "bNew")


def _keep_item(items, it):
    """Record a catalog item definition seen anywhere in the capture, stripped of instance state.

    Quantity is deliberately dropped rather than kept: on a reward or inventory row it is how
    many that player got, not the item's own stack definition."""
    if not isinstance(it, dict) or it.get("ID") is None or not it.get("Name"):
        return
    try:
        iid = int(it["ID"])
    except (TypeError, ValueError):
        return
    if iid in items:
        return
    items[iid] = {k: v for k, v in it.items() if k not in _INSTANCE_FIELDS}


def scan(path):
    """-> (maps, apops) mined from the capture.

    maps: mapName -> {"area": AreaJoin, "cells": {Frame: CellJoin}}
    apops: apopID(str) -> parsed apop object
    """
    maps, apops, quests, items, cur = {}, {}, {}, {}, None
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
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
                slot = maps.setdefault(m, {"area": None, "cells": {}, "placements": {}})
                # Keep the RICHEST AreaJoin as the map payload, not the first: a capture usually
                # holds several joins of one map, and with the Infinity markers installed some
                # are our own server's, which carry fewer placements than AE's.
                if slot["area"] is None or \
                        len(p.get("monBranch") or []) > len(slot["area"].get("monBranch") or []):
                    slot["area"] = p
                # ...but UNION the placements across every join. No single join is guaranteed to
                # hold them all — a player who passed through a town before and after a release
                # has both versions in the capture, and the newly added NPC is only in the later
                # one, which may not be the join with the most entries overall.
                for e in (p.get("monBranch") or []):
                    try:
                        slot["placements"].setdefault(int(e["MonMapID"]), e)
                    except (KeyError, TypeError, ValueError):
                        continue
            elif cmd == "CellJoin" and cur:
                maps[cur]["cells"].setdefault(p.get("Frame") or "Enter", p)
            elif cmd == "getApop":
                # apopData is apopID -> a JSON *string* of the apop object
                for k, v in (p.get("apopData") or {}).items():
                    if k in apops:
                        continue
                    try:
                        apops[str(k)] = json.loads(v) if isinstance(v, str) else v
                    except Exception:
                        continue
            elif cmd == "getQuests":
                for _qid, q in (p.get("quests") or {}).items():
                    if isinstance(q, dict) and q.get("QuestID") is not None:
                        quests.setdefault(int(q["QuestID"]), q)
                    for bucket in ("Static", "Random"):
                        for it in ((q.get("Rewards") or {}).get(bucket) or []):
                            _keep_item(items, it)
            for key in ("items", "loot", "bankedItems", "classItems"):
                for it in (p.get(key) or []):
                    _keep_item(items, it)
            _keep_item(items, p.get("item"))
    return maps, apops, quests, items


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("capture", nargs="?", default=str(DEFAULT_CAP))
    ap.add_argument("--apply", action="store_true", help="write the files (default: dry run)")
    ap.add_argument("--force-map", action="append", default=[],
                    help="rewrite this map's file even though we already have one")
    ap.add_argument("--merge-placements", action="append", default=[], metavar="MAP",
                    help="add missing monBranch placements to a map we already have")
    ap.add_argument("--bundle-floor", type=int, default=78600, metavar="ID",
                    help="import items whose art bundle id is at least this (default: the "
                         "2026 release band). Quest rewards are imported regardless.")
    args = ap.parse_args()

    cap = pathlib.Path(args.capture)
    if not cap.exists():
        print(f"no capture at {cap}")
        return 1

    maps, apops, quests, items = scan(cap)
    existing_apops = {}
    if APOPS_FILE.exists():
        try:
            existing_apops = json.loads(APOPS_FILE.read_text(encoding="utf-8")) or {}
        except Exception:
            existing_apops = {}

    new_maps, skipped, forced = [], [], []
    for name, data in sorted(maps.items()):
        if data["area"] is None:
            continue
        target = MAPS_DIR / f"{_safe(name)}.json"
        # The stored map carries the union of every placement seen for it, so one walk-through
        # that missed a corner does not write a map with that corner's NPCs absent.
        union = [data["placements"][k] for k in sorted(data["placements"])
                 if k < CUSTOM_PAD_FLOOR]
        if union:
            data["area"] = dict(data["area"], monBranch=union)
        if not target.exists():
            new_maps.append((name, data, target))
        elif name in args.force_map:
            forced.append((name, data, target))
        else:
            skipped.append(name)

    merges = []
    for name in args.merge_placements:
        target = MAPS_DIR / f"{_safe(name)}.json"
        if not target.exists():
            print(f"  (--merge-placements {name}: no map file to merge into — skipped)")
            continue
        captured = (maps.get(name) or {}).get("placements")
        if not captured:
            print(f"  (--merge-placements {name}: not in this capture — skipped)")
            continue
        doc, added, custom = merge_placements(name, captured, target)
        merges.append((name, target, doc, added, custom))

    new_apops = {k: v for k, v in apops.items() if k not in existing_apops}

    existing_quests = {}
    if QUESTS_FILE.exists():
        try:
            existing_quests = json.loads(QUESTS_FILE.read_text(encoding="utf-8")) or {}
        except Exception:
            existing_quests = {}
    new_quests = {str(k): v for k, v in quests.items() if str(k) not in existing_quests}

    existing_items = {}
    if ITEMS_FILE.exists():
        try:
            existing_items = json.loads(ITEMS_FILE.read_text(encoding="utf-8")) or {}
        except Exception:
            existing_items = {}
    # Only items the new content actually references: everything rewarded by a new quest, plus
    # anything whose art bundle is in the current release band. A capture also contains the whole
    # of one player's inventory and bank, and importing that would pull in years of unrelated
    # back-catalogue that no content on this server points at.
    wanted = set()
    for q in new_quests.values():
        for bucket in ("Static", "Random"):
            for it in ((q.get("Rewards") or {}).get(bucket) or []):
                try:
                    wanted.add(int(it["ID"]))
                except (KeyError, TypeError, ValueError):
                    continue
    for iid, it in items.items():
        bundle = it.get("Bundle")
        if isinstance(bundle, dict) and int(bundle.get("ID") or 0) >= args.bundle_floor:
            wanted.add(iid)
    new_items = {str(k): items[k] for k in sorted(wanted)
                 if k in items and str(k) not in existing_items}

    print(f"capture: {cap}")
    print(f"maps in capture: {len(maps)}   already have: {len(skipped)}")
    for name, data, _ in new_maps:
        area = data["area"]
        mons = area.get("monBranch") or []
        ids = sorted({m.get("MonID") for m in mons if m.get("MonID") is not None})
        print(f"  NEW MAP  {name:<16} cells={len(data['cells']):<3} "
              f"placements={len(mons):<3} monsters={ids}")
        print(f"           bundle={(area.get('Bundle') or {}).get('Filename')}")
    for name, data, _ in forced:
        print(f"  REWRITE  {name:<16} cells={len(data['cells'])} (--force-map)")
    for name, _t, _doc, added, custom in merges:
        print(f"  MERGE    {name:<16} +{len(added)} placements"
              f"{f' (skipped {len(custom)} of our own custom pads)' if custom else ''}")
        for e in added:
            print(f"           pad {e.get('MonMapID')}: MonID={e.get('MonID')} "
                  f"{e.get('strMonName')!r} apop={e.get('apopID')} frame={e.get('strFrame')}")
        for e in custom:
            print(f"           skipped pad {e.get('MonMapID')} {e.get('strMonName')!r} (ours)")
    print(f"apops in capture: {len(apops)}   new: {len(new_apops)}"
          f"  {sorted(int(k) for k in new_apops)}")
    print(f"items in capture: {len(items)}   new+referenced: {len(new_items)}")
    for iid in sorted(new_items, key=int):
        it = new_items[iid]
        b = it.get("Bundle") or {}
        print(f"  NEW ITEM {iid}: {it.get('Name')!r} type={it.get('ItemType')} "
              f"bundle={b.get('ID')} {b.get('Filename') or ''}")
    print(f"quests in capture: {len(quests)}   new: {len(new_quests)}")
    for qid in sorted(new_quests, key=int):
        q = new_quests[qid]
        turnins = len(q.get("turnin") or [])
        print(f"  NEW QUEST {qid}: {q.get('Name')!r} turnins={turnins} "
              f"gold={q.get('Gold')} exp={q.get('Exp')}")

    if not args.apply:
        print("\nDRY RUN — nothing written. Re-run with --apply.")
        return 0

    MAPS_DIR.mkdir(parents=True, exist_ok=True)
    for name, data, target in new_maps + forced:
        target.write_text(json.dumps(data), encoding="utf-8")
        print(f"  wrote {target.relative_to(ROOT)}")
    for name, target, doc, added, _custom in merges:
        if not added:
            continue
        target.write_text(json.dumps(doc), encoding="utf-8")
        print(f"  merged {len(added)} placements into {target.relative_to(ROOT)}")
    if new_apops:
        merged = dict(existing_apops)
        merged.update(new_apops)
        APOPS_FILE.write_text(json.dumps(merged), encoding="utf-8")
        print(f"  merged {len(new_apops)} apops into {APOPS_FILE.relative_to(ROOT)} "
              f"({len(existing_apops)} -> {len(merged)})")
    if new_items:
        mergedi = dict(existing_items)
        mergedi.update(new_items)
        ITEMS_FILE.write_text(json.dumps(mergedi), encoding="utf-8")
        print(f"  merged {len(new_items)} items into {ITEMS_FILE.relative_to(ROOT)} "
              f"({len(existing_items)} -> {len(mergedi)})")
    if new_quests:
        mergedq = dict(existing_quests)
        mergedq.update(new_quests)
        QUESTS_FILE.write_text(json.dumps(mergedq, separators=(",", ":")), encoding="utf-8")
        print(f"  merged {len(new_quests)} quests into {QUESTS_FILE.relative_to(ROOT)} "
              f"({len(existing_quests)} -> {len(mergedq)})")
    print("\nNext: re-seed so the new maps/monsters/apops land in the DB.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
