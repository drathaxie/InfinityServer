"""
Gems / enhancements (patterns) — the equip gate.

Enhanceable items (Weapon/Armor/Cape/Helm) need a Pattern before they can be equipped;
the client sends itemdefaultpattern[itemID] and the server must reply UpdatePattern with
a gem. Verifies: a weapon gem carries a damage Base, gear gems carry stat bonuses, the
applied gem persists on the item (shows in initPlayer so it stays equipped on relog), and
removePattern clears it.
"""
import json
import pathlib
import tempfile
import db
import seed
import game
import patterns
import combat


def _mk_item(conn, item_id, equip_spot, name):
    conn.execute(
        "INSERT INTO items(item_id, name, item_type, raw) VALUES(?,?,?,?) "
        "ON CONFLICT(item_id) DO UPDATE SET raw=excluded.raw",
        (item_id, name, 0,
         json.dumps({"ID": item_id, "Name": name, "EquipSpot": equip_spot, "Level": 10})))


def main():
    db.use_throwaway()
    seed.run()
    with db.connect() as c:
        c.execute("INSERT INTO accounts(username, password) VALUES('g','g')")
        acc = c.execute("SELECT id FROM accounts WHERE username='g'").fetchone()["id"]
        c.execute("INSERT INTO characters(account_id, name, gold) VALUES(?,?,0)", (acc, "g"))
        char = c.execute("SELECT * FROM characters WHERE name='g'").fetchone()

        _mk_item(c, 90001, patterns.WEAPON, "Test Blade")
        _mk_item(c, 90002, patterns.ARMOR, "Test Plate")
        for it in (90001, 90002):
            c.execute("INSERT INTO char_items(char_item_id, char_id, item_id, quantity, equipped) "
                      "VALUES(?,?,?,1,0)", (game._next_char_item_id(c), char["id"], it))
        c.commit()

        # before Power Up the weapon has no gem -> client wouldn't let you equip it
        inv = {i["ID"]: i for i in game.inventory(c, char["id"])}
        assert inv[90001].get("ItemPattern") is None

        # Power Up: weapon gem carries a damage Base
        rw = patterns.item_default_pattern(c, char["id"], 90001)
        assert rw["Cmd"] == "UpdatePattern" and rw["Success"]
        assert rw["pattern"]["EquipSpot"] == patterns.WEAPON and rw["pattern"]["Base"] > 0

        # gear gem carries stat bonuses, no weapon Base
        ra = patterns.item_default_pattern(c, char["id"], 90002)
        assert ra["pattern"]["END"] > 0 and ra["pattern"]["Base"] == 0

        # persists on the item (initPlayer carries ItemPattern -> stays empowered on relog)
        inv2 = {i["ID"]: i for i in game.inventory(c, char["id"])}
        assert inv2[90001].get("ItemPattern") is not None, "gem must persist"
        assert inv2[90001]["ItemPattern"]["Base"] == rw["pattern"]["Base"]

        # equipPattern addresses the item by its CATALOG ID (PatternPreview sends selectedItem.ID,
        # not a char_item_id) — resolve the owned instance, don't 404. (Regression: the client sent
        # item_id 17585 for a Blade whose char_item_id was 2842 -> "item not found".)
        re = patterns.equip_pattern(c, char["id"], 90001, 555, -1)
        assert re["Cmd"] == "UpdatePattern" and re["Success"], f"equipPattern by item ID must resolve, got {re}"
        assert re["pattern"]["Base"] == 31, "applied a Common weapon gem (Base 31)"

        # removePattern clears it
        cpid = c.execute("SELECT char_pattern_id FROM char_items WHERE char_id=? AND item_id=?",
                         (char["id"], 90001)).fetchone()["char_pattern_id"]
        rr = patterns.remove_pattern(c, char["id"], cpid)
        assert rr["Cmd"] == "removePattern"
        inv3 = {i["ID"]: i for i in game.inventory(c, char["id"])}
        assert inv3[90001].get("ItemPattern") is None, "removePattern must clear the gem"

        # --- KEYSTONE (capture 2026-06-18): gems are the source of stats + weapon damage ---
        # 1=1 anchors: the minted default weapon gem equals the captured "Common Weapon"
        # (UpdatePattern, packets.jsonl line 61958) at its Lvl 5, and its damage range matches
        # the in-game "27-34 DAMAGE" tooltip exactly (Base*(1-+Wild), truncated).
        assert patterns.default_pattern({"EquipSpot": patterns.WEAPON, "Level": 5}) == \
            patterns.COMMON_WEAPON_L5, "Power-Up mints the captured Common Weapon gem at Lvl 5"
        assert patterns.weapon_range(patterns.COMMON_WEAPON_L5) == (27, 34), \
            "weapon range = Base*(1-+Wild) = 27..34 (the captured tooltip)"
        assert patterns.weapon_range({"Base": 0}) is None, "a gear gem (no Base) has no weapon range"
        # Power Up mints a FIXED Common gem (Base 31) regardless of item level — capture: items
        # 300/17873/51605 all returned Base 31. (A Lvl-1 item must NOT get a scaled-down Base.)
        for lvl in (1, 5, 50):
            assert patterns.default_pattern({"EquipSpot": patterns.WEAPON, "Level": lvl})["Base"] == 31, \
                f"Power Up mints Base 31 at any item level, got level {lvl}"

        # Equip a Lvl-5 weapon alone, Power it up, and read the gem contribution in ISOLATION:
        # the weapon gem exposes the 27-34 range and its six stats fold in (LUK->LCK), no HP.
        c.execute("INSERT INTO items(item_id, name, item_type, raw) VALUES(90003,'Keystone Blade',0,?)"
                  "ON CONFLICT(item_id) DO UPDATE SET raw=excluded.raw",
                  (json.dumps({"ID": 90003, "Name": "Keystone Blade",
                               "EquipSpot": patterns.WEAPON, "Level": 5}),))
        c.execute("INSERT INTO char_items(char_item_id, char_id, item_id, quantity, equipped) "
                  "VALUES(?,?,90003,1,1)", (game._next_char_item_id(c), char["id"]))
        c.commit()
        patterns.item_default_pattern(c, char["id"], 90003)   # mint the Lvl-5 Common Weapon gem
        wbonus = game.pattern_bonus(c, char["id"])
        assert wbonus["weapon"] == (27, 34), f"equipped weapon gem range, got {wbonus['weapon']}"
        assert wbonus["STR"] == 6 and wbonus["INT"] == 6 and wbonus["DEX"] == 4, wbonus
        assert wbonus["LCK"] == 2, f"gem LUK must map to LCK, got {wbonus['LCK']}"
        assert wbonus["hp"] == 0, "a weapon gem grants no flat HP"

        # Now add a Lvl-5 helm: it grants flat HEALTH (captured "17 HEALTH") on top of stats.
        c.execute("INSERT INTO items(item_id, name, item_type, raw) VALUES(90004,'Keystone Helm',0,?)"
                  "ON CONFLICT(item_id) DO UPDATE SET raw=excluded.raw",
                  (json.dumps({"ID": 90004, "Name": "Keystone Helm",
                               "EquipSpot": patterns.HEAD, "Level": 5}),))
        c.execute("INSERT INTO char_items(char_item_id, char_id, item_id, quantity, equipped) "
                  "VALUES(?,?,90004,1,1)", (game._next_char_item_id(c), char["id"]))
        c.commit()
        patterns.item_default_pattern(c, char["id"], 90004)   # mint the helm (flat-HEALTH) gem
        bonus = game.pattern_bonus(c, char["id"])
        assert bonus["hp"] == 17, f"helm gem grants flat 17 HEALTH (captured tooltip), got {bonus['hp']}"
        assert bonus["weapon"] == (27, 34), "the weapon range is unchanged by the helm"
        assert bonus["STR"] == wbonus["STR"] + 3, "the helm gem's stats add to the weapon gem's"

    # gems ADD to the base stats (not replace) and fold into MaxHP
    base_char = {"id": 90099, "stat_str": 10, "stat_end": 10, "stat_dex": 10, "stat_int": 10,
                 "stat_wis": 10, "stat_lck": 10, "level": 5}
    sta0, hp0 = game.build_combat_stats(base_char)
    sta1, hp1 = game.build_combat_stats(base_char, bonus)
    assert sta1["STR"] == sta0["STR"] + bonus["STR"] and sta1["INT"] == sta0["INT"] + bonus["INT"], \
        "gem stats add over base"
    assert sta1["LCK"] == sta0["LCK"] + bonus["LCK"], "gem LUK adds to LCK"
    assert hp1 > hp0, f"equipped gems raise MaxHP ({hp0}->{hp1})"
    # isolate the FLAT helm HP term (17) from the gem's END-derived HP
    _, hp_no_flat = game.build_combat_stats(base_char, dict(bonus, hp=0))
    assert hp1 == hp_no_flat + 17, f"helm gem's flat 17 HEALTH folds into MaxHP ({hp_no_flat}->{hp1})"
    # tcr/scm track the gem-raised LCK/DEX (the CONFIRMED-exact secondary formulas)
    assert abs(sta1["tcr"] - (0.0005 * sta1["LCK"] + 0.0004 * sta1["DEX"])) < 1e-9
    assert abs(sta1["scm"] - (1.5 + 0.003 * sta1["LCK"])) < 1e-9

    # statUpdate's DmgMin/DmgMax IS the equipped weapon-gem range (27-34), not the ap fallback
    su_gem = game.build_stat_update(base_char, bonus=bonus)
    assert (su_gem["DmgMin"], su_gem["DmgMax"]) == (27, 34), "statUpdate carries the gem weapon range"
    su_none = game.build_stat_update(base_char)
    assert (su_none["DmgMin"], su_none["DmgMax"]) != (27, 34), "no gem -> ap-derived fallback range"
    # UpdatePattern.stats (1=1: the recompute a gem change carries back) is the full wire sta
    cs = game.combat_sta(base_char, bonus)
    assert cs["STR"] == sta1["STR"] and "cpo" in cs and "tha" in cs, "combat_sta = merged wire sta"

    # combat._hit ROLLS the equipped weapon-gem range (27-34) * a small power term, NOT ap*1.8..2.5.
    # Sample only non-crit (DT_NORMAL) hits so the band is the raw weapon roll (set_power forces a
    # small default crit chance; crits/dodges would blur the band).
    def _band(uid, weapon):
        combat.set_power(uid, {"ap": 24, "sp": 24, "tcr": 0.0, "scm": 1.5, "tha": 1.0}, weapon=weapon)
        xs = [d for d, dt in (combat._hit(f"p:{uid}", 1.0, False) for _ in range(20000))
              if dt == combat.DT_NORMAL]
        return min(xs), max(xs)
    glo, ghi = _band(9100, (27, 34))
    assert 28 <= glo and ghi <= 42, f"gem band [{glo},{ghi}] ~= weaponRoll(27-34)*~1.15 (ap 24)"
    # the no-gem fallback is the (higher) ap-derived band — proves the gem path is in force
    flo, fhi = _band(9101, None)
    assert flo > ghi, f"ap fallback band (min {flo}) sits above the gem band (max {ghi})"

    print(f"patterns OK: weapon gem Base={rw['pattern']['Base']}, gear gem END={ra['pattern']['END']}, "
          f"persists on item, removePattern clears it")
    print(f"KEYSTONE OK: default gem == captured Common Weapon (27-34 tooltip); gems add stats "
          f"(STR {sta0['STR']}->{sta1['STR']}) + HP ({hp0}->{hp1}); _hit rolls gem band "
          f"[{glo},{ghi}] not ap fallback [{flo},{fhi}]")
    print("ALL PATTERN TESTS PASSED")


if __name__ == "__main__":
    main()
