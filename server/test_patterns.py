"""
Gems / enhancements (patterns) - the equip gate.

Enhanceable items (Weapon/Armor/Cape/Helm) need a Pattern before they can be equipped;
the client sends itemdefaultpattern[itemID] and the server must reply UpdatePattern with
a gem. Verifies: a weapon gem carries a damage Base, gear gems carry stat bonuses, the
applied gem persists on the item (shows in initPlayer so it stays equipped on relog), and
removePattern clears it.
"""
import asyncio
import json

import db
import seed
import game
import patterns
import combat
import loot
from handlers import patterns_cmds


def _mk_item(conn, item_id, equip_spot, name, level=10):
    db.store_item(conn, {"ID": item_id, "Name": name, "EquipSpot": equip_spot, "Level": level}, replace=True)


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

        # --- gem bag (char_patterns / initPlayer.patterns[]): grant a loose gem, slot it onto
        #     gear via equipPattern (by the gear's CATALOG id + the bag gem's CharPatternID),
        #     and removePattern returns it to the bag (lossless). ---
        wgem = patterns.roll_pattern({"EquipSpot": patterns.WEAPON}, archetype="warrior", quality=9)
        cpid = patterns.grant_gem(c, char["id"], wgem)
        assert any(g["CharPatternID"] == cpid for g in patterns.loose_gems(c, char["id"])), \
            "a granted gem shows in the bag (initPlayer.patterns[])"
        re = patterns.equip_pattern(c, char["id"], 90001, cpid, -1)
        assert re["Cmd"] == "UpdatePattern" and re["Success"], f"equipPattern must resolve, got {re}"
        assert re["_consumedPatternID"] == cpid, "client must be told to remove the used gem"
        assert re["_bouncedPatternItem"]["pattern"]["Base"] == rw["pattern"]["Base"], \
            "the replaced Common gem returns to the bag/client as a PatternItem"
        assert re["pattern"]["Base"] == wgem["Base"] and re["pattern"]["STR"] == wgem["STR"], \
            "the gear gets the BAG gem's own stats (not a fixed Common)"
        assert not any(g["CharPatternID"] == cpid for g in patterns.loose_gems(c, char["id"])), \
            "the slotted gem leaves the bag"
        inv_g = {i["ID"]: i for i in game.inventory(c, char["id"])}
        assert inv_g[90001]["ItemPattern"]["Base"] == wgem["Base"], "applied gem persists on the item"
        # a wrong-slot gem is rejected (an Armor gem can't go on a Weapon)
        agem = patterns.roll_pattern({"EquipSpot": patterns.ARMOR}, archetype="wizard", quality=7)
        acp = patterns.grant_gem(c, char["id"], agem)
        bad = patterns.equip_pattern(c, char["id"], 90001, acp, -1)
        assert not bad["Success"] and "slot" in bad["errorMessage"].lower(), "slot mismatch is rejected"
        # a gem ITEM (ItemType 43) grants to the bag with archetype+slot from its name
        gpat = patterns.gem_item_pattern({"Name": "Wizard Armor Gem", "ItemType": 43}, quality=8)
        assert gpat["EquipSpot"] == patterns.ARMOR and gpat["INT"] > gpat["STR"], \
            "'Wizard Armor Gem' -> a wizard (INT) gem for the Armor slot"
        # removePattern is PatternPreview's Destroy Gem action: it deletes a LOOSE gem while
        # leaving the applied weapon enhancement intact.
        rr = patterns.remove_pattern(c, char["id"], acp)
        assert rr["Cmd"] == "removePattern"
        assert not any(g["CharPatternID"] == acp for g in patterns.loose_gems(c, char["id"])), \
            "Destroy Gem removes the loose PatternItem from persistence"
        inv3 = {i["ID"]: i for i in game.inventory(c, char["id"])}
        assert inv3[90001]["ItemPattern"]["Base"] == wgem["Base"], \
            "destroying a loose gem must not strip the equipped weapon"

        # RequestDustPattern is the real Delete/Dust button flow. It destroys exactly one owned
        # loose gem, increments the persistent currency once, and returns ResponseDust's native
        # fields. A replay or another character's id cannot mint dust.
        dust_gem = patterns.roll_pattern(
            {"EquipSpot": patterns.ARMOR}, archetype="wizard", quality=8)
        dust_id = patterns.grant_gem(c, char["id"], dust_gem)
        c.commit()
        before_dust = c.execute("SELECT dust FROM characters WHERE id=?", (char["id"],)).fetchone()["dust"]
        dr = patterns.dust_pattern(c, char["id"], dust_id)
        assert dr == {"Cmd": "dust", "bSuccess": True, "Gained": 40,
                      "Total": before_dust + 40, "CharPatternID": dust_id, "message": ""}
        assert not any(g["CharPatternID"] == dust_id for g in patterns.loose_gems(c, char["id"]))
        replay = patterns.dust_pattern(c, char["id"], dust_id)
        assert not replay["bSuccess"] and replay["Gained"] == 0 and replay["Total"] == dr["Total"], \
            "a repeated dust request cannot award twice"

        # The handler mirrors all three client mutations immediately: update the gear, remove
        # the consumed gem, and return the displaced old gem to the PatternItem bag.
        agem2 = patterns.roll_pattern({"EquipSpot": patterns.ARMOR}, archetype="wizard", quality=8)
        acp2 = patterns.grant_gem(c, char["id"], agem2)
        c.commit()

        class _Writer:
            def __init__(self):
                self.frames = []

            def write(self, raw):
                self.frames.append(json.loads(raw.rstrip(b"\x00")))

            async def drain(self):
                pass

        class _Member:
            uid = 99123

        class _Session:
            conn = c
            member = _Member()

            def __init__(self, current):
                self.char = current

        out = _Writer()
        asyncio.run(patterns_cmds.equip_pattern(
            _Session(char), out, "equipPattern", [90002, acp2, -1], {}))
        assert [p["Cmd"] for p in out.frames] == ["UpdatePattern", "removePattern", "addItems"]
        assert out.frames[1]["CharPatternID"] == acp2
        assert out.frames[2]["items"] == [] and len(out.frames[2]["patternItems"]) == 1
        assert out.frames[2]["patternItems"][0]["pattern"]["Base"] == ra["pattern"]["Base"]

        # The same request can dust a gem directly from the loot window. LootID selects the
        # pending drop; it must disappear without first entering char_patterns.
        loot_gem = {"ID": 99001, "Name": "Wizard Armor Gem", "ItemType": 43,
                    "ItemPattern": patterns.roll_pattern(
                        {"EquipSpot": patterns.ARMOR}, archetype="wizard", quality=7)}
        pending = loot.add_pending(_Member.uid, [loot_gem])[0]
        out2 = _Writer()
        asyncio.run(patterns_cmds.dust_pattern(
            _Session(char), out2, "dustPattern", [0, pending["LootID"]], {}))
        assert [frame["Cmd"] for frame in out2.frames] == [
            "dust", "removePattern", "getLoot",
        ]
        assert out2.frames[0]["bSuccess"] and out2.frames[0]["Gained"] == 20
        assert out2.frames[1]["CharPatternID"] == 0
        assert out2.frames[2] == {
            "Cmd": "getLoot", "bSuccess": True, "message": "",
            "ItemID": loot_gem["ID"], "LootID": pending["LootID"],
        }
        assert loot.pending(_Member.uid) == [], "dusted loot must leave the pending window"

        init = game.build_init_player(c, c.execute(
            "SELECT * FROM characters WHERE id=?", (char["id"],)).fetchone())
        assert init["playerInfo"]["Dust"] == before_dust + 60, \
            "the persisted Dust total must survive through initPlayer"

        # --- KEYSTONE (capture 2026-06-18): gems are the source of stats + weapon damage ---
        # 1=1 anchors: the minted default weapon gem equals the captured "Common Weapon"
        # (UpdatePattern, packets.jsonl line 61958) at its Lvl 5, and its damage range matches
        # the in-game "27-34 DAMAGE" tooltip exactly (Base*(1-+Wild), truncated).
        assert patterns.default_pattern({"EquipSpot": patterns.WEAPON, "Level": 5}) == \
            patterns.COMMON_WEAPON_L5, "Power-Up mints the captured Common Weapon gem at Lvl 5"
        assert patterns.weapon_range(patterns.COMMON_WEAPON_L5) == (27, 34), \
            "weapon range = Base*(1-+Wild) = 27..34 (the captured tooltip)"
        assert patterns.weapon_range({"Base": 0}) is None, "a gear gem (no Base) has no weapon range"
        # Power Up mints a FIXED Common gem (Base 31) regardless of item level - capture: items
        # 300/17873/51605 all returned Base 31. (A Lvl-1 item must NOT get a scaled-down Base.)
        for lvl in (1, 5, 50):
            assert patterns.default_pattern({"EquipSpot": patterns.WEAPON, "Level": lvl})["Base"] == 31, \
                f"Power Up mints Base 31 at any item level, got level {lvl}"

        # Equip a Lvl-5 weapon alone, Power it up, and read the gem contribution in ISOLATION:
        # the weapon gem exposes the 27-34 range and its six stats fold in (LUK->LCK), no HP.
        _mk_item(c, 90003, patterns.WEAPON, "Keystone Blade", level=5)
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
        _mk_item(c, 90004, patterns.HEAD, "Keystone Helm", level=5)
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
    # the no-gem fallback is the (higher) ap-derived band - proves the gem path is in force
    flo, fhi = _band(9101, None)
    assert flo > ghi, f"ap fallback band (min {flo}) sits above the gem band (max {ghi})"

    # --- class-based, rarity-scaled drop gems (roll_pattern) ---
    wpn = {"EquipSpot": patterns.WEAPON, "Level": 5}
    # archetype decides the pumped stat: a Warrior gem's biggest stat is STR, a Wizard gem's INT
    warr = patterns.roll_pattern(wpn, archetype="warrior", quality=8)
    assert max(warr, key=lambda k: warr[k] if k in ("STR", "INT", "DEX", "WIS", "END", "LUK") else -1) == "STR", \
        f"a warrior gem must pump STR, got {warr}"
    wiz = patterns.roll_pattern(wpn, archetype="wizard", quality=8)
    assert wiz["INT"] > wiz["STR"], f"a wizard gem must favour INT over STR, got {wiz}"
    assert patterns.roll_pattern(wpn, archetype="rogue", quality=8)["DEX"] >= 8, "rogue gem pumps DEX"
    assert patterns.roll_pattern(wpn, archetype="healer", quality=8)["WIS"] >= 8, "healer gem pumps WIS"
    # rarity IS strength: higher Quality -> higher weapon Base (Common 31 .. Mythic 230)
    assert patterns.roll_pattern(wpn, quality=5)["Base"] == 31
    assert patterns.roll_pattern(wpn, quality=10)["Base"] > patterns.roll_pattern(wpn, quality=7)["Base"]
    # the primary stat scales with rarity too (Mythic warrior STR >> Common warrior STR, on average)
    lo = sum(patterns.roll_pattern(wpn, archetype="warrior", quality=5)["STR"] for _ in range(200))
    hi = sum(patterns.roll_pattern(wpn, archetype="warrior", quality=10)["STR"] for _ in range(200))
    assert hi > lo * 1.6, f"Mythic warrior STR must dwarf Common ({lo/200:.1f} -> {hi/200:.1f})"
    # archetype inferred from a gem item's name; non-enhanceable items never roll a gem
    assert patterns.archetype_of("Wizard Armor Gem") == "wizard"
    assert patterns.roll_pattern({"EquipSpot": 1}, quality=9) is None, "a non-gear item can't be gemmed"
    print(f"drop-gems OK: warrior->STR wizard->INT rogue->DEX healer->WIS; rarity scales Base "
          f"(31..230) + primary stat ({lo/200:.0f}->{hi/200:.0f})")

    print(f"patterns OK: weapon gem Base={rw['pattern']['Base']}, gear gem END={ra['pattern']['END']}, "
          f"persists; apply syncs consume+bounce; Destroy Gem removes loose pattern")
    print(f"KEYSTONE OK: default gem == captured Common Weapon (27-34 tooltip); gems add stats "
          f"(STR {sta0['STR']}->{sta1['STR']}) + HP ({hp0}->{hp1}); _hit rolls gem band "
          f"[{glo},{ghi}] not ap fallback [{flo},{fhi}]")
    print("ALL PATTERN TESTS PASSED")


if __name__ == "__main__":
    main()
