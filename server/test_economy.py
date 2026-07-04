#!/usr/bin/env python3
"""
Offline test of the persistence + economy core (no game/socket needed).
Proves: account creation, persistent gold, buy deducts + grants item,
sell credits + removes, and that a re-login sees the saved state.
"""
import db, seed, game


def main():
    db.use_throwaway()      # isolated store so the suite can run in one process
    db.init()
    seed.run()
    conn = db.connect()

    # fresh test account (clear child rows first - FK constraints)
    conn.execute("DELETE FROM char_items WHERE char_id IN "
                 "(SELECT id FROM characters WHERE name='__test__')")
    conn.execute("DELETE FROM characters WHERE name='__test__'")
    conn.execute("DELETE FROM accounts WHERE username='__test__'")
    conn.commit()

    char = game.login(conn, "__test__", "pw")

    # fresh-start: a new character is a CLEAN level-1 slate (no gold, no inherited maxed loadout)
    # with just the base Warrior class + a pre-gemmed Default Sword.
    assert char["level"] == 1, f"fresh char starts at level 1, got {char['level']}"
    assert char["gold"] == 0, f"fresh char starts with no gold, got {char['gold']}"
    assert char["class_id"] == game.STARTER_CLASS_ID, "fresh char is the base Warrior class"
    _fresh_inv = {i["ID"]: i for i in game.inventory(conn, char["id"])}
    assert set(_fresh_inv) == {game.STARTER_CLASS_ITEM, game.STARTER_WEAPON_ITEM}, \
        f"fresh inventory is only class + weapon, got {list(_fresh_inv)}"
    assert _fresh_inv[game.STARTER_WEAPON_ITEM].get("ItemPattern", {}).get("Base") == 31, \
        "the Default Sword is pre-gemmed (Common gem, 27-34) so it's equippable + hits"
    print(f"fresh-start OK: lvl1, 0 gold, Warrior + pre-gemmed Default Sword")

    # the initPlayer must NOT leak the captured maxed account's gem inventory / pending loot /
    # house / friends - those reappearing on relog was the reported bug.
    _init = game.build_init_player(conn, char)
    for _k in ("loot", "patterns", "houseItems", "friends"):
        assert _init.get(_k) == [], f"initPlayer.{_k} must be empty for a fresh char, got {len(_init.get(_k) or [])}"
    assert all(i["ID"] in (game.STARTER_CLASS_ITEM, game.STARTER_WEAPON_ITEM) for i in _init["items"]), \
        "initPlayer.items is the char's own loadout, not the template's 255"
    print("initPlayer OK: no leaked gems/loot/house/friends from the captured template")

    # credit gold so the buy/sell economy below has something to spend (fresh chars have none)
    conn.execute("UPDATE characters SET gold=1000000 WHERE id=?", (char["id"],))
    conn.commit()
    char = conn.execute("SELECT * FROM characters WHERE id=?", (char["id"],)).fetchone()
    start_gold = char["gold"]
    start_inv = len(game.inventory(conn, char["id"]))
    print(f"login: char#{char['id']} gold={start_gold} inventory={start_inv}")

    # pick a real, NON-class, NON-house/furniture shop item to buy: class items are
    # non-sellable (P2-1), and houses/furniture (EquipSpot 8/9) deliberately never land in the
    # regular inventory (they reply houseItem, not item — see game.buy) — this test wants an
    # ordinary bag item, so skip both.
    row = None
    for r in conn.execute("SELECT shop_id, shop_item_id, cost, item_id FROM shop_items "
                          "ORDER BY shop_item_id"):
        idef = db.item(conn, r["item_id"]) or {}
        if game._is_class_item(conn, r["item_id"]):
            continue
        if int(idef.get("EquipSpot", 0) or 0) in (game.EQUIP_SPOT_HOUSE, game.EQUIP_SPOT_HOUSE_ITEM):
            continue
        row = r
        break
    assert row is not None, "need a non-class shop item to test buy/sell"
    print(f"buying shop={row['shop_id']} item={row['shop_item_id']} cost={row['cost']}")
    resp = game.buy(conn, char, ["0", str(row["shop_id"]), str(row["shop_item_id"])])
    char = conn.execute("SELECT * FROM characters WHERE id=?", (char["id"],)).fetchone()
    assert resp["Success"], resp
    assert char["gold"] == start_gold - row["cost"], (char["gold"], start_gold, row["cost"])
    assert len(game.inventory(conn, char["id"])) == start_inv + 1
    bought_item_id = resp["item"]["ID"]
    post_buy = len(game.inventory(conn, char["id"]))
    gold_after_buy = char["gold"]
    print(f"  -> Success, gold now {gold_after_buy}, item ID={bought_item_id}, inv={post_buy}")

    # sell it back (client sells by catalog item ID, not CharItemID)
    resp = game.sell(conn, char, [str(bought_item_id), "1"])
    char = conn.execute("SELECT * FROM characters WHERE id=?", (char["id"],)).fetchone()
    assert resp["Success"], resp
    assert resp["Amount"] > 0, resp
    assert char["gold"] >= gold_after_buy            # gold credited
    assert len(game.inventory(conn, char["id"])) <= post_buy   # instance removed or decremented
    print(f"  -> sold for {resp['Amount']}, gold now {char['gold']}, "
          f"inv={len(game.inventory(conn, char['id']))}")

    # re-login: state must persist
    char2 = game.login(conn, "__test__", "pw")
    assert char2["id"] == char["id"]
    assert char2["gold"] == char["gold"]
    print(f"re-login: same char#{char2['id']} gold={char2['gold']} (persisted)")

    # --- P2-1: class-item class points (CP) are not a sellable stack ---
    cls_id = seed.class_item_ids(conn)[0]
    # selling / dropping a class item is rejected (its Quantity is class points, not a stack)
    sresp = game.sell(conn, char, [str(cls_id), "1"])
    assert not sresp["Success"] and "Class" in sresp.get("Message", ""), "class items can't be sold"
    assert game.remove_item(conn, char, [str(cls_id), "1"]) is None, "class items can't be dropped"
    # the catalog/shop carries the purchase quantity (1), not a leaked maxed CP
    craw = db.item(conn, cls_id)
    assert craw.get("Quantity") == 1, f"catalog class-armor Quantity must be 1, got {craw.get('Quantity')}"
    # every OWNED class item has the consistent maxed CP (reconciled from 1/302499/302500)
    owned_q = [r["quantity"] for r in conn.execute(
        "SELECT DISTINCT quantity FROM char_items WHERE item_id=?", (cls_id,))]
    assert owned_q in ([], [seed.CLASS_CP_MAX]), f"owned class CP must be consistent, got {owned_q}"
    # granting a class item gives MAXED CP (playable), not a stack of 1
    cid = game._grant_item(conn, char["id"], craw)
    gq = conn.execute("SELECT quantity FROM char_items WHERE char_item_id=?", (cid,)).fetchone()["quantity"]
    assert gq == seed.CLASS_CP_MAX, f"granted class item must have maxed CP, got {gq}"
    conn.execute("DELETE FROM char_items WHERE char_item_id=?", (cid,)); conn.commit()
    print(f"P2-1 class CP OK: non-sellable/non-droppable, catalog Quantity=1, owned CP {seed.CLASS_CP_MAX} (consistent)")
    print("\nALL ECONOMY TESTS PASSED")


if __name__ == "__main__":
    main()
