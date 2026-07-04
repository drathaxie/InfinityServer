"""
Bank moves (bankFromInv / bankToInv / bankSwapInv — decomp: Request/ResponseInvToBank,
BankToInv, BankSwap). Proves: deposit/withdraw round-trip all of an item's rows, equipped and
class items refuse, slot caps hold (one slot per item id, matching the client's dict-by-ID
view), a swap is atomic (all-or-nothing), and refusals return None (the client only mutates
on the reply, so silence = nothing moved).
"""
import db
import seed
import game


def _side(conn, cid, item_id, banked):
    return conn.execute(
        "SELECT COUNT(*) AS n FROM char_items WHERE char_id=? AND item_id=? AND banked=?",
        (cid, item_id, 1 if banked else 0)).fetchone()["n"]


def _nonclass_items(conn, n):
    """n distinct non-class catalog item ids to play with."""
    out = []
    for r in conn.execute("SELECT item_id FROM items ORDER BY item_id"):
        if not game._is_class_item(conn, r["item_id"]) and r["item_id"] != game.STARTER_WEAPON_ITEM:
            out.append(r["item_id"])
            if len(out) == n:
                break
    assert len(out) == n, f"need {n} non-class catalog items, got {len(out)}"
    return out


def main():
    db.use_throwaway()
    seed.run()
    conn = db.connect()
    char = game.login(conn, "__banker__", "pw")
    cid = char["id"]
    a, b, c = _nonclass_items(conn, 3)

    # deposit: an owned bag item moves (all rows) to the bank; reply is the exact s2c shape
    game.give_item(conn, char, a, 2)
    resp = game.bank_deposit(conn, char, a)
    assert resp == {"Cmd": "InvToBank", "invID": a}, resp
    assert _side(conn, cid, a, banked=True) == 1 and _side(conn, cid, a, banked=False) == 0
    assert any(i["ID"] == a for i in game.bank(conn, cid)), "deposited item shows in LoadBank"

    # withdraw: it comes back
    resp = game.bank_withdraw(conn, char, a)
    assert resp == {"Cmd": "BankToInv", "bankID": a}, resp
    assert _side(conn, cid, a, banked=False) == 1 and _side(conn, cid, a, banked=True) == 0

    # refusals: equipped gear (starter sword) and class items never bank; unknown/unowned -> None
    assert game.bank_deposit(conn, char, game.STARTER_WEAPON_ITEM) is None, \
        "equipped item can't be banked"
    assert game.bank_deposit(conn, char, seed.class_item_ids(conn)[0]) is None, \
        "class item can't be banked (Quantity is class points)"
    assert game.bank_deposit(conn, char, 99999999) is None, "unowned item refuses"
    assert game.bank_withdraw(conn, char, a) is None, "item not in bank refuses withdraw"

    # swap: bag item and bank item trade places atomically
    game.give_item(conn, char, b, 1)
    assert game.bank_deposit(conn, char, b) is not None
    game.give_item(conn, char, c, 1)
    resp = game.bank_swap(conn, char, c, b)                 # c: bag->bank, b: bank->bag
    assert resp == {"Cmd": "BankSwap", "invID": c, "bankID": b}, resp
    assert _side(conn, cid, c, banked=True) == 1 and _side(conn, cid, b, banked=False) == 1

    # swap refusal is ALL-or-nothing: a bad bank side must roll back the deposit half
    assert game.bank_swap(conn, char, b, 99999999) is None, "bad bank side refuses"
    assert _side(conn, cid, b, banked=False) == 1, "refused swap moved NOTHING (atomic)"
    assert game.bank_swap(conn, char, b, b) is None, "same-id swap refuses"

    # slot caps: one slot per item id. With the cap at the current usage, a NEW id refuses
    # but topping up an id already on that side (no new slot) still works.
    used = game._slots_used(conn, cid, banked=True)
    old_cap, game.DEFAULT_BANK_SLOTS = game.DEFAULT_BANK_SLOTS, used
    try:
        game.give_item(conn, char, a, 1)
        assert game.bank_deposit(conn, char, a) is None, "full bank refuses a NEW item id"
        game.give_item(conn, char, c, 1)                    # c already has banked rows
        assert game.bank_deposit(conn, char, c) is not None, \
            "an id already in the bank still deposits (no new slot needed)"
    finally:
        game.DEFAULT_BANK_SLOTS = old_cap

    print("bank OK: deposit/withdraw/swap round-trip, equipped+class+unowned refuse, "
          "atomic swap, slot caps enforced")
    print("ALL BANK TESTS PASSED")


if __name__ == "__main__":
    main()
