"""
Sell/remove guard: an EQUIPPED item can't be sold or deleted out from under the avatar, and a
stack sale prefers a plain copy over a gemmed one so it can't clobber a per-instance gem roll.
(Before the fix, sell/removeItem picked the first owned row with no equipped filter, so selling
the item id of your worn weapon deleted the row — and its gem roll — while the HUD showed it on.)
"""
import db
import seed
import game


def _rows(conn, cid, item_id):
    return conn.execute(
        "SELECT char_item_id, equipped, pattern_json FROM char_items "
        "WHERE char_id=? AND item_id=? AND banked=0 ORDER BY char_item_id",
        (cid, item_id)).fetchall()


def main():
    db.use_throwaway()
    seed.run()
    conn = db.connect()
    char = game.login(conn, "seller", "pw")
    cid = char["id"]
    SWORD = game.STARTER_WEAPON_ITEM      # item 1: equipped + pre-gemmed on a fresh character

    start = _rows(conn, cid, SWORD)
    assert len(start) == 1 and start[0]["equipped"] and start[0]["pattern_json"], \
        "starter sword is equipped and gemmed"

    # add a second, PLAIN, unequipped copy (a separate row — a gemmed row never merges)
    game.give_item(conn, char, SWORD, 1)
    assert len(_rows(conn, cid, SWORD)) == 2, "plain copy is its own row"

    # selling picks the PLAIN unequipped copy, never the equipped gemmed one
    resp = game.sell(conn, char, [SWORD, 1])
    assert resp["Success"], "selling the plain copy succeeds"
    after = _rows(conn, cid, SWORD)
    assert len(after) == 1 and after[0]["equipped"] and after[0]["pattern_json"], \
        "the equipped gemmed sword survives; the plain copy is what sold"

    # now only the equipped copy remains: selling is refused (would strip the worn item + gem)
    resp2 = game.sell(conn, char, [SWORD, 1])
    assert not resp2["Success"] and "nequip" in resp2["Message"], \
        "can't sell the last, equipped copy"
    assert len(_rows(conn, cid, SWORD)) == 1, "the equipped sword is untouched by the refused sale"

    # removeItem is likewise refused for an equipped-only item
    assert game.remove_item(conn, char, [SWORD, 1]) is None, \
        "removeItem won't delete equipped gear"
    assert len(_rows(conn, cid, SWORD)) == 1, "still equipped after the refused remove"

    print("sell-guard OK: equipped gear is sell/remove-proof; plain copy preferred over gemmed")
    print("ALL SELL-GUARD TESTS PASSED")


if __name__ == "__main__":
    main()
