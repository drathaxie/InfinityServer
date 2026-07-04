#!/usr/bin/env python3
"""
Build the "Buy a House" shop (id 2800): both working house deeds (the Cottage and the
Kickstarter Flying Castle) plus every house-decor item in the catalog (EquipSpot 9 —
WallItem/FloorItem). Sold from Carl at the buyhouse map (see fix_house_apops.py for his
new shop button).

Pricing is the ITEM's, not the listing's: a shop_items row always mirrors its item's own
catalog Cost/Coins (game.shop_listing serves the catalog's price — a listing never charges
something the client wasn't shown, and the same item shows the same price in every shop it's
sold in, matching every real captured shop). Most furniture (259 of 291 items) was captured
with NO price at all (only ever seen in initPlayer/inventory captures, never a live
house-shop capture); backfill_prices() gives those a ONE-TIME catalog default — real
Coins-flagged items get a modest 10 Coins, the couple with no currency data at all get 500
gold — and never touches an item that already has a real captured price (idempotent).

Idempotent: safe to re-run (rebuilds shop 2800's shop_items from scratch each time; catalog
prices are backfilled once and then left alone).

Usage:
    python capture/build_buyhouse_shop.py

Run locally against SQLite, or on the VM with .pg.env sourced for live Postgres. After
building on the authoritative DB, refresh the repo mirror with server/export_catalog.py.
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "server"))
import db          # noqa: E402

SHOP_ID = 2800
COTTAGE = 1286
CASTLE = 200001
DEFAULT_COINS_PRICE = 10             # OURS — real Coins-flagged furniture with no captured price
DEFAULT_GOLD_PRICE = 500             # OURS — furniture with no price/currency data at all
EQUIP_SPOT_HOUSE_ITEM = 9


def backfill_prices(conn):
    """Give every house-decor item that has NO captured price a one-time catalog default
    (never touches an item that already has one). Returns how many were backfilled."""
    n = 0
    for r in conn.execute("SELECT item_id, coins FROM items WHERE equip_spot=? "
                          "AND (cost IS NULL OR cost=0)", (EQUIP_SPOT_HOUSE_ITEM,)):
        item = db.item(conn, r["item_id"])
        if item is None:
            continue
        if r["coins"]:
            item["Cost"], item["Coins"] = DEFAULT_COINS_PRICE, True
        else:
            item["Cost"] = DEFAULT_GOLD_PRICE
            item.pop("Coins", None)
        db.store_item(conn, item, replace=True)
        n += 1
    return n


def shop_items(conn):
    """[(item_id, cost, coins), ...] for every listing, mirrored straight from each item's
    OWN catalog price (never invented here) — both deeds, then all furniture."""
    out = []
    for item_id in (COTTAGE, CASTLE):
        item = db.item(conn, item_id)
        out.append((item_id, int(item.get("Cost", 0) or 0), bool(item.get("Coins"))))
    for r in conn.execute("SELECT item_id, cost, coins FROM items WHERE equip_spot=? "
                          "ORDER BY item_id", (EQUIP_SPOT_HOUSE_ITEM,)):
        out.append((r["item_id"], int(r["cost"] or 0), bool(r["coins"])))
    return out


def main():
    db.init()
    conn = db.connect()
    backfilled = backfill_prices(conn)
    db.store_shop(conn, {"shop": {"shopID": SHOP_ID, "Name": "Buy a House",
                                  "Location": "Menu,buyhouse"}}, shop_id=SHOP_ID, replace=True)
    conn.execute("DELETE FROM shop_items WHERE shop_id=?", (SHOP_ID,))
    items = shop_items(conn)
    for i, (item_id, cost, coins) in enumerate(items, start=1):
        conn.execute(
            "INSERT INTO shop_items(shop_id, shop_item_id, item_id, cost, coins, "
            "quantity_remain) VALUES(?,?,?,?,?,-1)", (SHOP_ID, i, item_id, cost, 1 if coins else 0))
    conn.commit()
    deeds = sum(1 for iid, *_ in items if iid in (COTTAGE, CASTLE))
    print(f"shop {SHOP_ID} 'Buy a House': {len(items)} listings "
          f"({deeds} house deeds, {len(items) - deeds} furniture; "
          f"{backfilled} catalog prices backfilled this run)")


if __name__ == "__main__":
    main()
