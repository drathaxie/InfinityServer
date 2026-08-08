"""Account profile changes and IoDA inventory-token redemption."""
import account_manager
import db
import game


def _item(item_id, name, stack=1):
    return {"ID": item_id, "Name": name, "Description": "", "ItemType": 1,
            "EquipSpot": 1, "Quantity": 1, "StackSize": stack, "Level": 1,
            "Element": 1, "Faction": 1, "ReqQuests": [], "boostValues": {}}


def main():
    db.use_throwaway()
    db.init()
    conn = db.connect()
    char = game.login(conn, "TokenTester", "original-pass")
    account = conn.execute("SELECT id FROM accounts WHERE username='TokenTester'").fetchone()
    token = _item(account_manager.TOKEN_ITEM_ID, "Golden Item of Digital Awesomeness", 11)
    prize = _item(880001, "Chosen Prize")
    db.store_item(conn, token)
    db.store_item(conn, prize)
    game._grant_item(conn, char["id"], token)
    game._grant_item(conn, char["id"], token)
    conn.commit()

    assert len(account_manager.catalog(conn, limit=5000)) == conn.execute(
        "SELECT COUNT(*) AS n FROM items").fetchone()["n"]

    assert account_manager.token_balance(conn, char["id"]) == 2
    ok, _, got = account_manager.redeem(conn, account["id"], prize["ID"])
    assert ok and got["ID"] == prize["ID"]
    assert account_manager.token_balance(conn, char["id"]) == 1
    assert conn.execute("SELECT quantity FROM char_items WHERE char_id=? AND item_id=?",
                        (char["id"], prize["ID"])).fetchone()["quantity"] == 1
    audit = conn.execute("SELECT * FROM token_redemptions").fetchone()
    assert audit["token_item_id"] == account_manager.TOKEN_ITEM_ID
    assert audit["item_id"] == prize["ID"]

    ok, _, _ = account_manager.redeem(conn, account["id"], account_manager.TOKEN_ITEM_ID)
    assert not ok and account_manager.token_balance(conn, char["id"]) == 1

    ok, _ = account_manager.change_username(conn, account["id"], "original-pass", "Renamed Tester")
    assert ok
    assert conn.execute("SELECT name FROM characters WHERE id=?", (char["id"],)).fetchone()["name"] == "Renamed Tester"
    ok, _ = account_manager.change_password(conn, account["id"], "original-pass", "replacement-pass")
    assert ok
    assert game.authenticate(conn, "Renamed Tester", "replacement-pass") is not None
    assert game.authenticate(conn, "Renamed Tester", "original-pass") is None

    # The account inventory exposes all owned items, not only redemption tokens.
    owned = account_manager.inventory(conn, char["id"])
    assert {x["itemId"] for x in owned} >= {token["ID"], prize["ID"]}

    # Selling an AC item creates a permanent buyback entry; buying it back charges
    # the exact AC proceeds and restores it to inventory.
    ac_item = _item(880002, "Buyback Blade")
    ac_item.update({"Cost": 400, "Coins": True})
    game._grant_item(conn, char["id"], ac_item)
    conn.execute("UPDATE characters SET coins=1000 WHERE id=?", (char["id"],))
    conn.commit()
    fresh_char = conn.execute("SELECT * FROM characters WHERE id=?", (char["id"],)).fetchone()
    sold = game.sell(conn, fresh_char, [ac_item["ID"], 1])
    assert sold["Success"] and sold["Coins"] and sold["Amount"] == 100
    history = account_manager.buyback_history(conn, account["id"])
    assert len(history) == 1 and history[0]["itemId"] == ac_item["ID"]
    ok, _, cost = account_manager.buy_back(conn, account["id"], history[0]["id"])
    assert ok and cost == 100
    assert not account_manager.buyback_history(conn, account["id"])
    assert conn.execute("SELECT 1 FROM char_items WHERE char_id=? AND item_id=?",
                        (char["id"], ac_item["ID"])).fetchone()
    conn.close()
    print("account manager OK: profile changes + atomic inventory-token exchange")


if __name__ == "__main__":
    main()
