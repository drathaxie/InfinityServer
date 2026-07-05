"""
The "Buy a House" shop (2800, capture/build_buyhouse_shop.py): both working house deeds +
all furniture, wired to Carl's new ItemShop button (apop 96) and Penny's fixed
OpenHouseInventory button (apop 116, capture/fix_house_apops.py).

Pricing (OURS, by design): every house deed and every furniture item is a free AC item —
Cost 0, Coins True — the same shape every real captured founder/backer reward uses. Pricing
lives on the ITEM (game.shop_listing serves the catalog's own Cost/Coins, never a per-listing
override), so the SAME item shows/charges the SAME price in every shop selling it — the
Kickstarter Flying Castle deed is free everywhere it's listed, including the ip25-gated
Infinity Backer Shop (2688).
"""
import json

import db
import seed
import game

COTTAGE = 1286
CASTLE = 200001
BUYHOUSE_SHOP = 2800
BACKER_SHOP = 2688


def main():
    db.use_throwaway()
    seed.run()
    conn = db.connect()

    import pathlib
    import sys
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "capture"))
    import build_buyhouse_shop
    import fix_house_apops
    build_buyhouse_shop.main()                # DB-only (shop content); safe to call directly
    fix_house_apops.apply_to_db(conn)         # DB-only — main() also writes data/apops.json,
                                              # which a test must never do as a side effect

    # --- shop content: everything free (Cost 0, Coins True) ---
    resp = game.load_shop(conn, BUYHOUSE_SHOP)
    assert resp["shop"]["Name"] == "Buy a House"
    by_id = {it["ID"]: it for it in resp["shop"]["items"]}
    assert COTTAGE in by_id and CASTLE in by_id, "both working houses are listed"
    assert len(by_id) >= 290, f"all house furniture should be listed, got {len(by_id)}"
    assert all(int(it.get("EquipSpot", 0)) in (game.EQUIP_SPOT_HOUSE, game.EQUIP_SPOT_HOUSE_ITEM)
               for it in resp["shop"]["items"]), "only houses/furniture in this shop"
    assert all(it.get("Cost", -1) == 0 and it.get("Coins") is True
              for it in resp["shop"]["items"]), "every listing is a free AC item"

    # --- one canonical price per item: the SAME deed is free in every shop it's listed in ---
    castle_item = db.item(conn, CASTLE)
    assert castle_item["Cost"] == 0 and castle_item["Coins"] is True
    backer = game.load_shop(conn, BACKER_SHOP)
    backer_castle = next(it for it in backer["shop"]["items"] if it["ID"] == CASTLE)
    assert backer_castle["Cost"] == 0 and backer_castle["Coins"] is True, \
        "the castle deed is free everywhere it's sold, not just in the general shop"

    # --- buying grants a houseItem (never the bag `item`) and auto-equips a first home;
    # a free AC item costs the buyer nothing regardless of their gold/Coins balance ---
    buyer = game.login(conn, "__shopper__", "pw")
    assert buyer["gold"] == 0 and buyer["coins"] == 0, "a broke fresh character can still buy"
    castle_shop_item_id = next(
        r["shop_item_id"] for r in conn.execute(
            "SELECT shop_item_id FROM shop_items WHERE shop_id=? AND item_id=?",
            (BUYHOUSE_SHOP, CASTLE)))
    resp = game.buy(conn, buyer, ["0", str(BUYHOUSE_SHOP), str(castle_shop_item_id)])
    assert resp["Success"] and resp["Cost"] == 0, resp
    assert resp.get("houseItem") and "item" not in resp
    buyer = conn.execute("SELECT * FROM characters WHERE id=?", (buyer["id"],)).fetchone()
    assert buyer["gold"] == 0 and buyer["coins"] == 0, "a free item deducts nothing"
    eq = game.auto_equip_first_house(conn, buyer, CASTLE)
    assert eq is not None, "first house purchase auto-equips"
    assert game.equipped_house_id(conn, buyer["id"]) == CASTLE

    # --- apop wiring ---
    carl = json.loads(conn.execute("SELECT raw FROM apops WHERE apop_id=96").fetchone()["raw"])
    btn = next(el for p in carl["panels"] for el in p.get("elements", [])
              if el.get("type") == "Button")
    assert btn["action"] == "ItemShop" and int(btn["intMin"]) == BUYHOUSE_SHOP

    penny = json.loads(conn.execute("SELECT raw FROM apops WHERE apop_id=116").fetchone()["raw"])
    pbtn = next(el for p in penny["panels"] for el in p.get("elements", [])
               if el.get("type") == "Button" and el.get("ID") == 33)
    assert pbtn["action"] == "OpenHouseInventory" and pbtn["requirements"] == []

    # both scripts are idempotent (re-running doesn't duplicate the button or shop rows, or
    # change the already-free pricing)
    build_buyhouse_shop.main()
    fix_house_apops.apply_to_db(conn)
    resp2 = game.load_shop(conn, BUYHOUSE_SHOP)
    assert len(resp2["shop"]["items"]) == len(by_id), "re-running the shop build doesn't duplicate"
    assert db.item(conn, CASTLE)["Cost"] == 0, "re-running keeps the item free"
    carl2 = json.loads(conn.execute("SELECT raw FROM apops WHERE apop_id=96").fetchone()["raw"])
    n_buttons = sum(1 for p in carl2["panels"] for el in p.get("elements", [])
                   if el.get("type") == "Button")
    assert n_buttons == 1, "re-running the apop fix doesn't duplicate Carl's button"

    print(f"buyhouse shop OK: {len(by_id)} listings, all free AC items, "
          f"buy->houseItem->auto-equip (broke buyer, 0 deducted), Carl+Penny wired, "
          f"both scripts idempotent")
    print("ALL BUYHOUSE SHOP TESTS PASSED")


if __name__ == "__main__":
    main()
