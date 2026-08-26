#!/usr/bin/env python3
"""Apply an explicit, reviewed source-catalog delta to the configured DB.

Nothing is inferred: callers must list the item/shop IDs approved for import.
Default is dry-run; --apply performs the upserts. Monster/map source files are
handled by the normal idempotent server seed on restart.
"""
import argparse
import datetime as dt
import json
import pathlib
import shutil
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "server"))
import db  # noqa: E402


def ids(value):
    return [int(part) for part in (value or "").split(",") if part.strip()]


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--items", default="")
    ap.add_argument("--shops", default="")
    ap.add_argument("--bundles", default="")
    ap.add_argument("--delta", help="JSON containing only {items:{},shops:{}} to import")
    ap.add_argument("--registry", help="client asset_bundles.json to merge explicit bundles into")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    item_ids, shop_ids, bundle_ids = ids(args.items), ids(args.shops), ids(args.bundles)
    if args.delta:
        delta = json.loads(pathlib.Path(args.delta).read_text(encoding="utf-8"))
        items, shops = delta.get("items") or {}, delta.get("shops") or {}
        bundles = delta.get("bundles") or {}
    else:
        items = json.loads((ROOT / "data" / "items.json").read_text(encoding="utf-8"))
        shops = json.loads((ROOT / "data" / "shops.json").read_text(encoding="utf-8"))
        bundles = json.loads((ROOT / "data" / "asset_bundles.json").read_text(encoding="utf-8"))
    missing_files = ([i for i in item_ids if str(i) not in items]
                     + [s for s in shop_ids if str(s) not in shops]
                     + [b for b in bundle_ids if str(b) not in bundles])
    if missing_files:
        raise SystemExit(f"requested IDs absent from source catalog: {missing_files}")
    print(f"target={db.BACKEND} items={item_ids} shops={shop_ids} bundles={bundle_ids} "
          f"apply={args.apply}")
    if not args.apply:
        return
    db.init()
    with db.connect() as conn:
        for iid in item_ids:
            db.store_item(conn, items[str(iid)], replace=True)
        for sid in shop_ids:
            source = shops[str(sid)]
            db.store_shop(conn, source.get("meta") or {}, shop_id=sid)
            for link in source.get("items") or []:
                conn.execute(
                    "INSERT INTO shop_items(shop_id,shop_item_id,item_id,cost,coins,quantity_remain) "
                    "VALUES(?,?,?,?,?,?) ON CONFLICT(shop_id,shop_item_id) DO UPDATE SET "
                    "item_id=excluded.item_id,cost=excluded.cost,coins=excluded.coins,"
                    "quantity_remain=excluded.quantity_remain",
                    (sid, link["shop_item_id"], link["item_id"], link["cost"],
                     link["coins"], link["quantity_remain"]))
        for bid in bundle_ids:
            row = bundles[str(bid)]
            conn.execute(
                "INSERT INTO asset_bundles(bundle_id,name,type,filename,version_content," 
                "version_stage,version_live,dependency_id) VALUES(?,?,?,?,?,?,?,?) "
                "ON CONFLICT(bundle_id) DO UPDATE SET name=excluded.name,type=excluded.type," 
                "filename=excluded.filename,version_content=excluded.version_content," 
                "version_stage=excluded.version_stage,version_live=excluded.version_live," 
                "dependency_id=excluded.dependency_id",
                (bid, row.get("Name"), row.get("Type"),
                 row.get("FileName") or row.get("Filename"),
                 int(row.get("VersionContent") or 0), int(row.get("VersionStage") or 0),
                 int(row.get("VersionLive") or 0), int(row.get("DependencyID") or 0)))
        conn.commit()
    if args.registry and bundle_ids:
        registry_path = pathlib.Path(args.registry)
        registry = json.loads(registry_path.read_text(encoding="utf-8"))
        backup = registry_path.with_name(
            f"{registry_path.name}.bak-{dt.datetime.now():%Y%m%d-%H%M%S}")
        shutil.copy2(registry_path, backup)
        for bid in bundle_ids:
            registry[str(bid)] = bundles[str(bid)]
        ordered = {key: registry[key] for key in sorted(registry, key=int)}
        registry_path.write_text(json.dumps(ordered, indent=2), encoding="utf-8")
        print(f"registry merged={len(bundle_ids)} backup={backup}")
    print(f"applied items={len(item_ids)} shops={len(shop_ids)} bundles={len(bundle_ids)}")


if __name__ == "__main__":
    main()
