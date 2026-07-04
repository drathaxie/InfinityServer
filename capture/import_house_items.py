#!/usr/bin/env python3
r"""
Import house items (deeds + furniture) into the items catalog from captured houseItem defs.

Sources scanned:
  1. data/maps/*.json — captured house map docs embed the captured player's houseData
     (mapHouseData.items), e.g. the cottage capture carries suswolf's 81 furniture defs.
  2. a live packet capture (packets.jsonl) if present/given — initPlayer.houseItems lists and
     any AreaJoin houseData in it.

A houseItem is NOT the full catalog wire shape ({ItemID, Bundle, PrefabName, sType, sName,
iCost, bCoins, sDesc, MobileCompatibility}), so each is converted to a catalog item def:
ItemType from sType (House=23 / WallItem=24 / FloorItem=25 — the client's iType enum),
EquipSpot 8 for deeds / 9 for furniture, House=true. An existing COMPLETE catalog row (has a
Name and a Bundle) is never clobbered — these converted defs are leaner than a real one.

Usage:
    python capture/import_house_items.py [path/to/packets.jsonl]

Run locally against SQLite, or on the VM with .pg.env sourced for live Postgres. After an
import on the authoritative DB, refresh the repo seeds with server/export_catalog.py.
"""
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "server"))
import db          # noqa: E402

MAPS_DIR = pathlib.Path(__file__).resolve().parent.parent / "data" / "maps"
DEFAULT_CAP = pathlib.Path(r"C:\Program Files (x86)\Steam\steamapps\common"
                           r"\AdventureQuest Worlds Unity Playtest\UserData\Beyond\packets.jsonl")

# sType -> (ItemType, EquipSpot): the client's iType enum / EquipSpots enum
_STYPE = {"House": (23, 8), "WallItem": (24, 9), "FloorItem": (25, 9)}


def to_catalog(hi):
    """One houseItem dict -> a catalog item def (None if it isn't usable)."""
    try:
        iid = int(hi.get("ItemID") or 0)
    except (TypeError, ValueError):
        return None
    stype = hi.get("sType") or ""
    if iid <= 0 or stype not in _STYPE:
        return None
    itype, spot = _STYPE[stype]
    return {
        "ID": iid,
        "Name": hi.get("sName") or "",
        "Description": hi.get("sDesc") or "",
        "ItemType": itype,
        "EquipSpot": spot,
        "Level": 1,
        "Quantity": 1,
        "StackSize": 1,
        "Cost": int(hi.get("iCost") or 0),
        "Coins": bool(hi.get("bCoins")),
        "Bundle": hi.get("Bundle"),
        "PrefabName": hi.get("PrefabName") or "",
        "House": True,
        "MobileCompatibility": int(hi.get("MobileCompatibility") or 1),
        "Element": 1,
        "Faction": 1,
    }


def from_map_docs():
    """Every houseItem embedded in the captured map docs' houseData."""
    out = {}
    for p in sorted(MAPS_DIR.glob("*.json")):
        try:
            doc = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        items = ((doc.get("area") or {}).get("houseData") or {}).get("items") or []
        n = 0
        for hi in items:
            d = to_catalog(hi if isinstance(hi, dict) else {})
            if d is not None:
                out[d["ID"]] = d
                n += 1
        if n:
            print(f"  {p.name}: {n} houseItem defs")
    return out


def from_packets(cap_path):
    """houseItem defs from a live capture: initPlayer.houseItems + AreaJoin houseData.items."""
    out = {}
    if not cap_path.exists():
        return out
    with open(cap_path, encoding="utf-8", errors="replace") as f:
        for line in f:
            if '"houseItems"' not in line and '"houseData"' not in line:
                continue
            try:
                pkt = (json.loads(line) or {}).get("pkt") or {}
            except Exception:
                continue
            buckets = []
            if isinstance(pkt.get("houseItems"), list):
                buckets.append(pkt["houseItems"])
            hd = pkt.get("houseData")
            if isinstance(hd, dict) and isinstance(hd.get("items"), list):
                buckets.append(hd["items"])
            for bucket in buckets:
                for hi in bucket:
                    d = to_catalog(hi if isinstance(hi, dict) else {})
                    if d is not None:
                        out[d["ID"]] = d
    if out:
        print(f"  {cap_path.name}: {len(out)} houseItem defs")
    return out


def main():
    cap = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_CAP
    defs = from_map_docs()
    for iid, d in from_packets(cap).items():
        defs.setdefault(iid, d)             # map-doc defs win (same source quality; stable)
    print(f"{len(defs)} distinct house item defs collected")

    db.init()
    conn = db.connect()
    new = upgraded = kept = 0
    for iid, d in sorted(defs.items()):
        existing = db.item(conn, iid)
        if existing is None:
            db.store_item(conn, d)
            new += 1
        elif not (existing.get("Name") and existing.get("Bundle")):
            db.store_item(conn, d, replace=True)    # placeholder row -> real def
            upgraded += 1
        else:
            kept += 1                                # already a complete catalog def
    conn.commit()
    print(f"imported: {new} new, {upgraded} upgraded placeholders, {kept} already complete")


if __name__ == "__main__":
    main()
