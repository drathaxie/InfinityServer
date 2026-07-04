"""
The "Buy a House" shop (2800, capture/build_buyhouse_shop.py): both working house deeds +
all furniture, wired to Carl's new ItemShop button (apop 96) and Penny's fixed
OpenHouseInventory button (apop 116, capture/fix_house_apops.py).

Pricing is the ITEM's: game.shop_listing serves the catalog's own Cost/Coins, never a
per-listing override, so the SAME item shows (and charges) the SAME price in every shop it's
sold in — matching every real captured shop (founder-reward items are Cost 0 in the
catalog, not specially zeroed in one shop and priced in another). The Kickstarter Flying
Castle deed is consequently priced the same in this general shop AND in the ip25-gated
Infinity Backer Shop (2688) it's also listed in.
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

    # --- shop content ---
    resp = game.load_shop(conn, BUYHOUSE_SHOP)
    assert resp["shop"]["Name"] == "Buy a House"
    by_id = {it["ID"]: it for it in resp["shop"]["items"]}
    assert COTTAGE in by_id and CASTLE in by_id, "both working houses are listed"
    assert len(by_id) >= 290, f"all house furniture should be listed, got {len(by_id)}"
    assert all(int(it.get("EquipSpot", 0)) in (game.EQUIP_SPOT_HOUSE, game.EQUIP_SPOT_HOUSE_ITEM)
               for it in resp["shop"]["items"]), "only houses/furniture in this shop"
    assert all(it.get("Cost", 0) > 0 for it in resp["shop"]["items"]), \
        "every listing has a real price (backfilled if the capture had none)"

    # --- one canonical price per item: the SAME deed shows (and charges) the SAME price in
    # every shop it's listed in — this is the whole point of the shop_listing fix ---
    castle_item = db.item(conn, CASTLE)
    assert by_id[CASTLE]["Cost"] == castle_item["Cost"] and not by_id[CASTLE].get("Coins")
    backer = game.load_shop(conn, BACKER_SHOP)
    backer_castle = next(it for it in backer["shop"]["items"] if it["ID"] == CASTLE)
    assert backer_castle["Cost"] == by_id[CASTLE]["Cost"], \
        "the SAME castle deed costs the SAME everywhere — no per-shop divergence"
    assert by_id[COTTAGE]["Cost"] == 1000, "the Cottage keeps its real captured price"

    # --- buying grants a houseItem (never the bag `item`) and auto-equips a first home ---
    buyer = game.login(conn, "__shopper__", "pw")
    conn.execute("UPDATE characters SET gold=1000000 WHERE id=?", (buyer["id"],))
    conn.commit()
    buyer = conn.execute("SELECT * FROM characters WHERE id=?", (buyer["id"],)).fetchone()
    castle_shop_item_id = next(
        r["shop_item_id"] for r in conn.execute(
            "SELECT shop_item_id FROM shop_items WHERE shop_id=? AND item_id=?",
            (BUYHOUSE_SHOP, CASTLE)))
    price = castle_item["Cost"]
    resp = game.buy(conn, buyer, ["0", str(BUYHOUSE_SHOP), str(castle_shop_item_id)])
    assert resp["Success"] and resp["Cost"] == price, resp
    assert resp.get("houseItem") and "item" not in resp
    buyer = conn.execute("SELECT * FROM characters WHERE id=?", (buyer["id"],)).fetchone()
    assert buyer["gold"] == 1000000 - price
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

    # both scripts are idempotent (re-running doesn't duplicate the button, shop rows, or
    # re-backfill an already-priced item)
    build_buyhouse_shop.main()
    fix_house_apops.apply_to_db(conn)
    resp2 = game.load_shop(conn, BUYHOUSE_SHOP)
    assert len(resp2["shop"]["items"]) == len(by_id), "re-running the shop build doesn't duplicate"
    assert db.item(conn, CASTLE)["Cost"] == price, "re-running doesn't re-backfill a priced item"
    carl2 = json.loads(conn.execute("SELECT raw FROM apops WHERE apop_id=96").fetchone()["raw"])
    n_buttons = sum(1 for p in carl2["panels"] for el in p.get("elements", [])
                   if el.get("type") == "Button")
    assert n_buttons == 1, "re-running the apop fix doesn't duplicate Carl's button"

    print(f"buyhouse shop OK: {len(by_id)} listings, one canonical price per item "
          f"(castle {price}g in both shops it's sold in), buy->houseItem->auto-equip, "
          f"Carl+Penny wired, both scripts idempotent")
    print("ALL BUYHOUSE SHOP TESTS PASSED")


if __name__ == "__main__":
    main()
