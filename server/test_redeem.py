"""
Heromart redeem codes (RedeemCodeModal — the client hub's native "Redeem Code" button).

Ground truth (decomp Assembly-CSharp RedeemCodeModal): the client hardcodes
https://account.aq.com/webapi/Heromart/{Recent,RedeemNow} regardless of Main.WebApiURL, sends
`Authorization: Bearer {charid}:{sToken}`, and POSTs RedeemNow's `code` as a URL query param (its
WWWForm field is built but never attached — dead code in the shipped client). The mod patches
those two calls at our WebApiURL instead; game.redeem_code/redeem_history + webapi._heromart_auth
serve them. Response shape must match HeromartRedeemResponse exactly: {success, message,
rewardDesc} — the client deserializes it as-is.

A code can carry SEVERAL rewards (redeem_code_rewards, one row per reward) all granted together
on a single redemption — see game.redeem_code / game._apply_redeem_reward.
"""
import json
import time

import db
import seed
import game
import webapi


def _mint(conn, code, *rewards, description="", max_uses=0):
    """rewards: (reward_type, value, qty) tuples, same shape /addcode builds one at a time."""
    conn.execute(
        "INSERT INTO redeem_codes (code, description, max_uses, created) VALUES (?, ?, ?, ?)",
        (code, description, max_uses, time.time()))
    for reward_type, value, qty in rewards:
        conn.execute(
            "INSERT INTO redeem_code_rewards (code, reward_type, reward_value, reward_qty) "
            "VALUES (?, ?, ?, ?)", (code, reward_type, value, qty))
    conn.commit()


def main():
    db.use_throwaway()
    seed.run()
    conn = db.connect()
    char = game.login(conn, "__redeemer__", "pw")
    token = game.issue_token(conn, char["account_id"])

    # unknown code -> friendly failure, nothing granted
    r = game.redeem_code(conn, char, "NOPE")
    assert r == {"success": False, "message": "That code is not valid.", "rewardDesc": ""}, r

    # empty code (the client's own "please enter a code" guard, mirrored server-side too)
    r = game.redeem_code(conn, char, "   ")
    assert r["success"] is False and "enter a code" in r["message"].lower()

    # a code with no rewards attached is refused rather than silently granting nothing
    _mint(conn, "EMPTYCODE")
    r = game.redeem_code(conn, char, "EMPTYCODE")
    assert r["success"] is False and "misconfigured" in r["message"].lower(), r

    # single gold reward
    _mint(conn, "GOLD5K", ("gold", 5000, 1))
    before = int(char["gold"] or 0)
    r = game.redeem_code(conn, char, "gold5k")            # case-insensitive
    assert r["success"] is True and r["rewardDesc"] == "5000 Gold", r
    char = conn.execute("SELECT * FROM characters WHERE id=?", (char["id"],)).fetchone()
    assert int(char["gold"]) == before + 5000, "gold ADDS (not sets)"

    # one redemption per account: same code again is refused, no double-grant
    r2 = game.redeem_code(conn, char, "GOLD5K")
    assert r2["success"] is False and "already redeemed" in r2["message"].lower(), r2
    char = conn.execute("SELECT * FROM characters WHERE id=?", (char["id"],)).fetchone()
    assert int(char["gold"]) == before + 5000, "a refused redemption must not grant again"

    # a code with MULTIPLE rewards: gold + item + a founder achievement bit, all in one redemption
    item_id = next(row["item_id"] for row in conn.execute("SELECT item_id FROM items ORDER BY item_id")
                    if not game._is_class_item(conn, row["item_id"])
                    and row["item_id"] != game.STARTER_WEAPON_ITEM)
    _mint(conn, "BUNDLE", ("gold", 500, 1), ("item", item_id, 2), ("achievement", 0b101, 1))
    gold_before = int(char["gold"])
    r = game.redeem_code(conn, char, "BUNDLE")
    assert r["success"] is True, r
    assert "500 Gold" in r["rewardDesc"] and "Founder Tier" in r["rewardDesc"], r
    char = conn.execute("SELECT * FROM characters WHERE id=?", (char["id"],)).fetchone()
    assert int(char["gold"]) == gold_before + 500, "the gold leg of a bundle still applies"
    owned = sum(row["quantity"] for row in conn.execute(
        "SELECT quantity FROM char_items WHERE char_id=? AND item_id=?", (char["id"], item_id)))
    assert owned == 2, f"the item leg of a bundle still applies, got {owned}"
    ach = json.loads(char["achievements"] or "{}")
    assert ach.get("ip25", 0) & 0b101 == 0b101, "the achievement leg OR's into ip25"

    # a second achievement code ADDS bits rather than clobbering the first code's bits
    _mint(conn, "MOREFOUNDER", ("achievement", 0b010, 1))
    r = game.redeem_code(conn, char, "MOREFOUNDER")
    assert r["success"] is True, r
    char = conn.execute("SELECT * FROM characters WHERE id=?", (char["id"],)).fetchone()
    ach = json.loads(char["achievements"] or "{}")
    assert ach.get("ip25", 0) & 0b111 == 0b111, "achievement bits accumulate across codes"

    # an item code with a misconfigured (non-catalog) item id fails cleanly, no partial grant
    _mint(conn, "BADITEM", ("gold", 999, 1), ("item", 99999999, 1))
    gold_before = int(char["gold"])
    r = game.redeem_code(conn, char, "BADITEM")
    assert r["success"] is False and "misconfigured" in r["message"].lower(), r
    char = conn.execute("SELECT * FROM characters WHERE id=?", (char["id"],)).fetchone()
    assert int(char["gold"]) == gold_before, "a misconfigured code must not partially grant"

    # a global max_uses cap is enforced across accounts, independent of the per-account check
    _mint(conn, "LIMITED", ("gold", 100, 1), max_uses=1)
    other = game.login(conn, "__redeemer2__", "pw")
    r = game.redeem_code(conn, char, "LIMITED")
    assert r["success"] is True, r
    r = game.redeem_code(conn, other, "LIMITED")
    assert r["success"] is False and "limit" in r["message"].lower(), r

    # a deactivated code (the /delcode path) is refused just like an unknown one
    conn.execute("UPDATE redeem_codes SET active=0 WHERE code='LIMITED'")
    conn.commit()
    r = game.redeem_code(conn, other, "LIMITED")
    assert r["success"] is False and "not valid" in r["message"].lower(), r

    # history: only successful redemptions recorded, newest first
    hist = game.redeem_history(conn, char["account_id"])
    assert len(hist) == 4, hist   # GOLD5K, BUNDLE, MOREFOUNDER, LIMITED (BADITEM never recorded)
    assert hist[0]["desc"] == "100 Gold", "newest redemption (LIMITED) first"

    # RedeemCodeModal's TMP_InputField can hand back a code with an invisible zero-width space
    # stuck to it (seen live: 'welcome​')  must still match the stored code
    _mint(conn, "ZWSPCODE", ("gold", 1, 1))
    r = game.redeem_code(conn, char, "zwspcode​")
    assert r["success"] is True, r

    # webapi's bearer-token auth: {charid}:{sToken}, scoped to that exact character
    resolved = webapi._heromart_auth(conn, {"Authorization": f"Bearer {char['id']}:{token}"})
    assert resolved is not None and resolved["id"] == char["id"]
    assert webapi._heromart_auth(conn, {"Authorization": f"Bearer {char['id']}:wrong-token"}) is None
    assert webapi._heromart_auth(conn, {"Authorization": "Bearer not-shaped-right"}) is None
    assert webapi._heromart_auth(conn, {}) is None
    # a token that's valid for a DIFFERENT character must not authorize this one
    other_token = game.issue_token(conn, other["account_id"])
    assert webapi._heromart_auth(
        conn, {"Authorization": f"Bearer {char['id']}:{other_token}"}) is None

    # the web editor (redeem/list, redeem/load, redeem/save): full round trip
    payload = {
        "redeem": {"code": "EDITORTEST", "description": "", "max_uses": 5, "active": 1},
        "rewards": [
            {"reward_type": "gold", "reward_value": 1234, "reward_qty": 1},
            {"reward_type": "item", "reward_value": item_id, "reward_qty": 3},
            {"reward_type": "achievement", "reward_value": 4, "reward_qty": 1, "reward_field": "ip25"},
        ],
    }
    r = webapi.redeem_save(conn, {"json": [json.dumps(payload)]})
    assert r["ok"], r
    assert any(it["ID"] == "EDITORTEST" for it in webapi.redeem_list(conn, ""))
    loaded = webapi.redeem_load(conn, "ID=EDITORTEST")
    assert loaded["redeem"]["max_uses"] == 5 and len(loaded["rewards"]) == 3, loaded

    # re-saving REPLACES the reward set wholesale (drop one, save again -> only 2 remain)
    payload["rewards"].pop()
    assert webapi.redeem_save(conn, {"json": [json.dumps(payload)]})["ok"]
    assert len(webapi.redeem_load(conn, "ID=EDITORTEST")["rewards"]) == 2

    # a non-catalog item reward is rejected before anything is written
    bad = {"redeem": {"code": "BADEDIT", "description": "", "max_uses": 0, "active": 1},
           "rewards": [{"reward_type": "item", "reward_value": 999999999, "reward_qty": 1}]}
    r = webapi.redeem_save(conn, {"json": [json.dumps(bad)]})
    assert not r["ok"], r
    assert not any(it["ID"] == "BADEDIT" for it in webapi.redeem_list(conn, ""))

    # an editor-built code is actually redeemable end to end
    editor_char = game.login(conn, "__editortest__", "pw")
    r = game.redeem_code(conn, editor_char, "EDITORTEST")
    assert r["success"], r

    print("ALL REDEEM TESTS PASSED")


if __name__ == "__main__":
    main()
