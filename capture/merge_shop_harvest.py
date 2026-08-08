#!/usr/bin/env python3
"""Merge authoritative AE shop item definitions into data/items.json.

Default is report-only. --apply-missing adds only unknown ItemIDs and preserves
all existing/customized catalog rows.
"""
import argparse
import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
SOURCE = ROOT / "data" / "items.json"
HARVEST = ROOT / "capture" / "harvest" / "shop_items_live.jsonl"
SHOPS_SOURCE = ROOT / "data" / "shops.json"
SHOPS_HARVEST = ROOT / "capture" / "harvest" / "shops_live.json"


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply-missing", action="store_true")
    args = ap.parse_args()
    source = json.loads(SOURCE.read_text(encoding="utf-8"))
    harvested = {}
    for line in HARVEST.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
            harvested[str(int(row["ID"]))] = row
        except (ValueError, TypeError, KeyError, json.JSONDecodeError):
            continue
    missing = sorted(set(harvested) - set(source), key=int)
    changed = sorted((key for key in set(harvested) & set(source)
                      if harvested[key] != source[key]), key=int)
    print(f"harvested={len(harvested)} source={len(source)} missing={len(missing)} "
          f"changed_existing={len(changed)}")
    for key in missing:
        print(f"  + {key} {harvested[key].get('Name','')}")
    if args.apply_missing and missing:
        for key in missing:
            source[key] = harvested[key]
        ordered = {key: source[key] for key in sorted(source, key=int)}
        SOURCE.write_text(json.dumps(ordered, separators=(",", ":")), encoding="utf-8")
        print(f"applied={len(missing)} (existing rows preserved)")

    shops_source = json.loads(SHOPS_SOURCE.read_text(encoding="utf-8"))
    packets = json.loads(SHOPS_HARVEST.read_text(encoding="utf-8"))
    missing_shops = []
    normalized = {}
    for sid, packet in packets.items():
        shop = (packet or {}).get("shop") or {}
        items = shop.get("items") or []
        if not items:  # do not turn a server placeholder/REUSE response into a shop
            continue
        meta_shop = {key: value for key, value in shop.items() if key != "items"}
        meta_shop["items"] = []
        links = []
        for index, item in enumerate(items, 1):
            qremain = item.get("QuantityRemain")
            links.append({
                "shop_item_id": int(item.get("ShopItemID") or index),
                "item_id": int(item["ID"]),
                "cost": int(item.get("Cost") or 0),
                "coins": 1 if item.get("Coins") else 0,
                "quantity_remain": int(qremain) if qremain is not None else -1,
            })
        normalized[str(int(sid))] = {"meta": {"Cmd": "loadShop", "shop": meta_shop},
                                     "items": links}
        if str(int(sid)) not in shops_source:
            missing_shops.append(str(int(sid)))
    missing_shops.sort(key=int)
    print(f"harvested_shops={len(normalized)} source_shops={len(shops_source)} "
          f"missing_shops={len(missing_shops)}")
    for sid in missing_shops:
        print(f"  + shop {sid} {normalized[sid]['meta']['shop'].get('Name','')}")
    if args.apply_missing and missing_shops:
        for sid in missing_shops:
            shops_source[sid] = normalized[sid]
        ordered_shops = {key: shops_source[key] for key in sorted(shops_source, key=int)}
        SHOPS_SOURCE.write_text(json.dumps(ordered_shops, separators=(",", ":")), encoding="utf-8")
        print(f"applied_shops={len(missing_shops)} (existing shops preserved)")


if __name__ == "__main__":
    main()
