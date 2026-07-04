#!/usr/bin/env python3
"""
Build the "Buy a House" shop (id 2800): both working house deeds (the Cottage and the
Kickstarter Flying Castle) plus every house-decor item in the catalog (EquipSpot 9 —
WallItem/FloorItem). Sold from Carl at the buyhouse map (see fix_house_apops.py for his
new shop button).

Pricing (OURS, by design): every house deed and every piece of house furniture is a FREE AC
item — Cost 0, Coins True. Real captured backer/founder rewards use exactly this shape
(Cost 0, Coins 1 — never gold, never a nonzero price), so this reuses that convention rather
than inventing a new one. make_free_ac() sets it unconditionally on the catalog EVERY run
(overriding any earlier captured/backfilled price, including the deed's stale free-then-
priced history from earlier sessions) and re-syncs the SAME price onto every OTHER shop that
also sells one of these items (a shop_items row is a price snapshot, not a live reference to
the catalog — see game.shop_listing, which serves the catalog's price, not the listing's, so
catalog and every shop selling it must always agree).

Idempotent: safe to re-run anywhere, any number of times.

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
EQUIP_SPOT_HOUSE_ITEM = 9


def _furniture_ids(conn):
    return [r["item_id"] for r in conn.execute(
        "SELECT item_id FROM items WHERE equip_spot=?", (EQUIP_SPOT_HOUSE_ITEM,))]


def make_free_ac(conn):
    """Every house deed + furniture item: Cost 0, Coins True, on the catalog AND on every
    shop_items row selling it (any shop, not just 2800). Returns the full list of item ids
    touched (both deeds + all furniture)."""
    ids = [COTTAGE, CASTLE] + _furniture_ids(conn)
    for item_id in ids:
        item = db.item(conn, item_id)
        if item is None:
            continue
        item["Cost"], item["Coins"] = 0, True
        db.store_item(conn, item, replace=True)
    ph = ",".join("?" for _ in ids)
    conn.execute(f"UPDATE shop_items SET cost=0, coins=1 WHERE item_id IN ({ph})", ids)
    return ids


def shop_items(conn, ids):
    """[(item_id, cost, coins), ...] for every listing, mirrored straight from each item's
    OWN catalog price — both deeds, then all furniture."""
    out = []
    for item_id in ids:
        item = db.item(conn, item_id)
        out.append((item_id, int(item.get("Cost", 0) or 0), bool(item.get("Coins"))))
    return out


def main():
    db.init()
    conn = db.connect()
    ids = make_free_ac(conn)
    db.store_shop(conn, {"shop": {"shopID": SHOP_ID, "Name": "Buy a House",
                                  "Location": "Menu,buyhouse"}}, shop_id=SHOP_ID, replace=True)
    conn.execute("DELETE FROM shop_items WHERE shop_id=?", (SHOP_ID,))
    items = shop_items(conn, ids)
    for i, (item_id, cost, coins) in enumerate(items, start=1):
        conn.execute(
            "INSERT INTO shop_items(shop_id, shop_item_id, item_id, cost, coins, "
            "quantity_remain) VALUES(?,?,?,?,?,-1)", (SHOP_ID, i, item_id, cost, 1 if coins else 0))
    conn.commit()
    deeds = sum(1 for iid, *_ in items if iid in (COTTAGE, CASTLE))
    print(f"shop {SHOP_ID} 'Buy a House': {len(items)} listings "
          f"({deeds} house deeds, {len(items) - deeds} furniture) — all free AC items")


if __name__ == "__main__":
    main()
