"""Stock-client Heromart code reward and one-use behavior."""
import db
import game
import seed


def main():
    db.use_throwaway()
    db.init()
    conn = db.connect()
    char = game.login(conn, "code-tester", "test-password")
    token = {"ID": 50727, "Name": "Golden Item of Digital Awesomeness",
             "Description": "", "ItemType": 1, "EquipSpot": 1, "Quantity": 1,
             "StackSize": 11, "Level": 1, "Element": 1, "Faction": 1,
             "ReqQuests": [], "boostValues": {}}
    db.store_item(conn, token)
    conn.execute("INSERT INTO redeem_codes(code,description,max_uses,active,created) "
                 "VALUES('AWESOME10','10x Golden Item of Digital Awesomeness',0,1,0)")
    conn.execute("INSERT INTO redeem_code_rewards(code,reward_type,reward_value,reward_qty) "
                 "VALUES('AWESOME10','item',50727,10)")
    conn.commit()

    result = game.redeem_code(conn, char, " AWESOME10\u200b ")
    assert result["success"] and result["rewardDesc"] == "10x Golden Item of Digital Awesomeness"
    owned = conn.execute("SELECT quantity FROM char_items WHERE char_id=? AND item_id=50727",
                         (char["id"],)).fetchone()
    assert owned and int(owned["quantity"]) == 10
    again = game.redeem_code(conn, char, "awesome10")
    assert not again["success"] and "already" in again["message"].lower()
    assert len(game.redeem_history(conn, char["account_id"])) == 1

    catalog = __import__("json").loads(seed.ITEMS_FILE.read_text(encoding="utf-8"))
    for item_id in (978712, 978718, 978719):
        db.store_item(conn, catalog[str(item_id)])
    assert seed.seed_redeem_codes(conn) == 3
    before_coins = int(conn.execute("SELECT coins FROM characters WHERE id=?",
                                    (char["id"],)).fetchone()["coins"])
    result = game.redeem_code(conn, char, "dshark")
    assert result["success"] and "10,000 AdventureCoins" in result["rewardDesc"]
    assert int(conn.execute("SELECT coins FROM characters WHERE id=?",
                            (char["id"],)).fetchone()["coins"]) == before_coins + 10000
    owned_ids = {int(r["item_id"]) for r in conn.execute(
        "SELECT item_id FROM char_items WHERE char_id=? AND item_id IN (978712,978718,978719)",
        (char["id"],)).fetchall()}
    assert owned_ids == {978712, 978718, 978719}

    jon_items = {
        936854: ("Runes Of Time", 2, "items/maces/36854_RunesOfTime.unity3d"),
        948141: ("Mage of Time", 7, "armors/48141_DnoMoT.unity3d"),
        903309: ("Earthen Locks", 3, "items/helms/3309_autumn-hair01a.unity3d"),
        950646: ("Warrior of Time's Wrap", 4, "items/capes/50646_DnoWoTC.unity3d"),
        950639: ("Mage of Time's Tome Pet", 4, "items/capes/50639_DnoMoTC.unity3d"),
    }
    for item_id, (name, equip_spot, filename) in jon_items.items():
        item = db.item(conn, item_id)
        assert item and item["Name"] == name and int(item["EquipSpot"]) == equip_spot
        assert item["Filename"] == filename and int(item["Bundle"]["ID"]) > 0

    result = game.redeem_code(conn, char, "thankyoujon")
    assert result["success"] and result["rewardDesc"].startswith("Thank you, Jon!")
    owned_ids = {int(r["item_id"]) for r in conn.execute(
        "SELECT item_id FROM char_items WHERE char_id=? AND item_id IN (903309,936854,948141,950639,950646)",
        (char["id"],)).fetchall()}
    assert owned_ids == set(jon_items)
    second_char = game.login(conn, "code-tester-two", "test-password")
    exhausted = game.redeem_code(conn, second_char, "THANKYOUJON")
    assert not exhausted["success"] and "limit" in exhausted["message"].lower()

    result = game.redeem_code(conn, char, "undeadfounder")
    assert result["success"] and result["rewardDesc"] == "Infinity: Underworld Founder Achievement"
    achievements = __import__("json").loads(conn.execute(
        "SELECT achievements FROM characters WHERE id=?", (char["id"],)).fetchone()["achievements"])
    assert int(achievements.get("ip25", 0)) & (1 << 4)
    again = game.redeem_code(conn, char, "UNDEADFOUNDER")
    assert not again["success"] and "already" in again["message"].lower()
    conn.close()
    print("redeem code OK: grants items and achievements, seeds exclusive art, enforces account/global limits")


if __name__ == "__main__":
    main()
