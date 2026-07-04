#!/usr/bin/env python3
r"""
Backfill REAL item definitions (with Name + Description) into the items table from a live
packet capture — the organic, authoritative source we settled on (no REST item endpoint exists;
shipped bundles carry no description; the socket item catalog is access-gated). Every item def the
client legitimately received in normal play is in the capture: shop listings (loadShop), the
player's own inventory (initPlayer), and granted/looted items (addItems).

These are ground-truth AE defs, so they REPLACE any earlier placeholder row (the filename-derived
name + empty Description we inserted from the bundle catalog) for the same item id.

Usage:
    python capture/import_capture_items.py [path/to/packets.jsonl]
"""
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "server"))
import db          # noqa: E402

DEFAULT_CAP = pathlib.Path(r"C:\Program Files (x86)\Steam\steamapps\common"
                           r"\AdventureQuest Worlds Unity Playtest\UserData\Beyond\packets.jsonl")

# per-instance fields that ride on an inventory/shop item but aren't part of the catalog def
INSTANCE_FIELDS = ("ShopItemID", "QuantityRemain", "CharItemID", "LootID", "Banked",
                   "Equipped", "purchaseDate", "classRank")


def item_defs(cap_path):
    """Yield every distinct catalog item def seen in the capture (by item ID)."""
    seen = {}
    with open(cap_path, encoding="utf-8", errors="replace") as f:
        for line in f:
            if '"Description"' not in line and '"ItemType"' not in line:
                continue
            try:
                o = json.loads(line)
            except Exception:
                continue
            pkt = o.get("pkt") or {}
            buckets = []
            shop = pkt.get("shop")
            if isinstance(shop, dict):
                buckets.append(shop.get("items") or [])
            user = pkt.get("user")
            if isinstance(user, dict):
                inv = user.get("inventory") or user.get("items")
                if isinstance(inv, list):
                    buckets.append(inv)
            for key in ("items", "bankedItems"):
                if isinstance(pkt.get(key), list):
                    buckets.append(pkt[key])
            for bucket in buckets:
                for it in bucket:
                    if not isinstance(it, dict) or it.get("ID") is None:
                        continue
                    if "Name" not in it and "ItemType" not in it:
                        continue
                    iid = int(it["ID"])
                    d = {k: v for k, v in it.items() if k not in INSTANCE_FIELDS}
                    seen[iid] = d       # last one wins (all identical catalog-wise)
    return seen


def main():
    cap = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_CAP
    if not cap.exists():
        sys.exit(f"capture not found: {cap}")

    defs = item_defs(cap)
    print(f"{len(defs)} distinct real item defs in the capture")

    db.init()
    conn = db.connect()
    upgraded = new = 0
    for iid, it in sorted(defs.items()):
        existing = db.item(conn, iid)
        db.store_item(conn, it, replace=True)   # authoritative live def wins over any placeholder
        if existing is None:
            new += 1
        elif not (existing.get("Description") or "").strip():
            upgraded += 1
    conn.commit()
    print(f"imported {len(defs)} real item defs ({new} new, {upgraded} upgraded from placeholder)")
    # show the house items we just landed
    for iid, it in sorted(defs.items()):
        if it.get("House") or it.get("ItemType") == 23:
            print(f"  house item {iid}: {it.get('Name')!r} — {(it.get('Description') or '')[:80]}")


if __name__ == "__main__":
    main()
