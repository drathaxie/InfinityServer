#!/usr/bin/env python3
"""
Stock the DEV "check everything" shop with the ENTIRE item catalog, so any item
can be grabbed in-game for testing. Useful when a real shop's contents are gated
(founder/staff) and couldn't be captured: pull the item into the catalog (e.g.
via a bank/shop capture) and it shows up here.

Re-runnable: rebuilds the shop's listing from whatever is currently in `items`
(free, unlimited stock). Non-destructive to other shops — only touches this one.

Usage:  python server/fill_dev_shop.py [shopID]      (default 2722)
"""
import sys

import db

SHOP_ID = int(sys.argv[1]) if len(sys.argv) > 1 else 2722
NAME = "Dev Check All"


def fill(conn, shop_id=SHOP_ID, name=NAME):
    # Ensure the shop meta exists and shows a name (the captured 2722 came back empty).
    meta = db.shop_meta(conn, shop_id) or {}
    if not meta.get("Name"):
        meta = {k: v for k, v in meta.items() if k != "items"}   # meta never embeds items
        meta["shopID"], meta["Name"] = shop_id, name
        db.store_shop(conn, meta, shop_id=shop_id, replace=True)

    # Rebuild the listing: one free, unlimited shop_item per catalog item. shop_item_id
    # = item_id (unique within the shop; buy() keys on (shop_id, shop_item_id)).
    conn.execute("DELETE FROM shop_items WHERE shop_id=?", (shop_id,))
    ids = [r[0] for r in conn.execute("SELECT item_id FROM items ORDER BY item_id").fetchall()]
    for iid in ids:
        conn.execute(
            "INSERT INTO shop_items(shop_id, shop_item_id, item_id, cost, coins, "
            "quantity_remain) VALUES(?,?,?,0,0,-1)", (shop_id, iid, iid))
    return len(ids)


def main():
    db.init()
    with db.connect() as conn:
        n = fill(conn)
        conn.commit()
    print(f"[devshop] shop {SHOP_ID} '{NAME}' stocked with {n} items (free, unlimited)")


if __name__ == "__main__":
    main()
