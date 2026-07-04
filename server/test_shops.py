#!/usr/bin/env python3
"""
Offline test of the normalized item catalog (no game/socket needed).

Proves: shops no longer embed item JSON — items live in their own table and
shop_items reference them — yet the assembled loadShop response is identical to
the captured one, and buy/sell still work against the catalog.
"""
import json
import pathlib

import db

SAMPLE = pathlib.Path(__file__).resolve().parent.parent / "capture" / "samples" / "loadShop.json"


def main():
    db.use_throwaway()

    import seed
    import game

    db.init()
    seed.run()
    conn = db.connect()

    if SAMPLE.exists():
        sample = json.loads(SAMPLE.read_text(encoding="utf-8"))
    else:
        shops = json.loads((pathlib.Path(__file__).resolve().parent.parent / "data" / "shops.json")
                           .read_text(encoding="utf-8"))
        shop_id_s, stored = next(iter(shops.items()))
        sample = json.loads(json.dumps(stored["meta"]))
        shop = sample.get("shop") if isinstance(sample.get("shop"), dict) else sample
        shop["items"] = []
        for li in stored.get("items") or []:
            it = db.item(conn, li["item_id"])
            it["ShopItemID"] = li["shop_item_id"]
            it["QuantityRemain"] = li["quantity_remain"]
            shop["items"].append(it)
    shop_id = int(sample["shop"]["shopID"])

    # 1) Storage is normalized: items in their own table; shop_items carry no item JSON.
    si_cols = db._columns(conn, "shop_items")
    assert "raw" not in si_cols, "shop_items must not embed item JSON anymore"
    n_items = conn.execute("SELECT COUNT(*) FROM items").fetchone()[0]
    n_links = conn.execute("SELECT COUNT(*) FROM shop_items WHERE shop_id=?", (shop_id,)).fetchone()[0]
    assert n_items >= len(sample["shop"]["items"]), (n_items, len(sample["shop"]["items"]))
    assert "raw" not in db._columns(conn, "shops"), "shops must be canonical columns, not a raw blob"
    stored_shop = db.shop_blob(conn, shop_id)
    assert stored_shop["shop"]["items"] == [], "stored shop meta must not embed items"
    print(f"normalized: items table={n_items}, shop_items links={n_links}, shop meta items=[]")

    # 2) Assembled loadShop is byte-faithful to the capture (same items, same fields).
    resp = game.load_shop(conn, shop_id)
    got = {it["ShopItemID"]: it for it in resp["shop"]["items"]}
    want = {it["ShopItemID"]: it for it in sample["shop"]["items"]}
    assert got.keys() == want.keys(), (sorted(got), sorted(want))
    for sid, wi in want.items():
        gi = got[sid]
        assert gi == wi, f"item {sid} differs:\n got={json.dumps(gi, sort_keys=True)}\n want={json.dumps(wi, sort_keys=True)}"
    assert resp["shop"]["Name"] == sample["shop"]["Name"]
    print(f"loadShop rebuilt {len(got)} items, all identical to capture")

    # 3) The catalog is shared: a second shop reusing an item adds no duplicate item row.
    before = conn.execute("SELECT COUNT(*) FROM items").fetchone()[0]
    seed._seed_shop(conn, {"Cmd": "loadShop", "shop": {
        "shopID": 999999, "Name": "Reuse", "items": [sample["shop"]["items"][0]]}})
    conn.commit()
    after = conn.execute("SELECT COUNT(*) FROM items").fetchone()[0]
    assert after == before, "reusing an existing item must not duplicate the items row"
    assert conn.execute("SELECT COUNT(*) FROM shop_items WHERE shop_id=999999").fetchone()[0] == 1
    print(f"item reuse: items table stayed at {after} (one item, two shops)")

    # 4) Economy still works end to end against the catalog.
    conn.execute("DELETE FROM char_items WHERE char_id IN (SELECT id FROM characters WHERE name='__shoptest__')")
    conn.execute("DELETE FROM characters WHERE name='__shoptest__'")
    conn.execute("DELETE FROM accounts WHERE username='__shoptest__'")
    conn.commit()
    char = game.login(conn, "__shoptest__", "pw")
    # fresh chars start with no gold; credit some so the buy below can run
    conn.execute("UPDATE characters SET gold=1000000 WHERE id=?", (char["id"],))
    conn.commit()
    char = conn.execute("SELECT * FROM characters WHERE id=?", (char["id"],)).fetchone()
    priced = conn.execute("SELECT * FROM shop_items WHERE shop_id=? AND cost>0 LIMIT 1", (shop_id,)).fetchone()
    start_gold = char["gold"]
    resp = game.buy(conn, char, ["0", str(shop_id), str(priced["shop_item_id"])])
    assert resp["Success"], resp
    char = conn.execute("SELECT * FROM characters WHERE id=?", (char["id"],)).fetchone()
    assert char["gold"] == start_gold - priced["cost"], (char["gold"], start_gold, priced["cost"])
    assert resp["item"].get("ShopItemID") is None, "granted item must not carry shop-instance fields"
    print(f"buy: ID={resp['item']['ID']} for {priced['cost']}g, gold {start_gold}->{char['gold']}")
    resp = game.sell(conn, char, [str(resp["item"]["ID"]), "1"])
    assert resp["Success"] and resp["Amount"] > 0, resp
    print(f"sell: credited {resp['Amount']}")

    print("\nALL SHOP/ITEM TESTS PASSED")


if __name__ == "__main__":
    main()
