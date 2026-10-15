"""Compatibility coverage for the 2026-09-17 client contract.

The commands this build added that the server now answers: getClassSkills (class preview for a
class the player does not own), getMonDrops (the drop-inspection panel) and getRandomStatues
(pedestal assignment). See docs/CLIENT_DELTA_2026-09-17.md.
"""
import asyncio
import json
from types import SimpleNamespace

import db
import forge
import game
import loot
import seed
import statues
from handlers import items as item_handlers
from handlers import loot_cmds
from handlers import players as player_handlers


class Writer:
    """Collects the framed JSON packets a handler writes."""

    def __init__(self):
        self.packets = []

    def write(self, payload):
        self.packets.append(json.loads(payload.rstrip(b"\x00").decode("utf-8")))

    async def drain(self):
        return None


def _session(conn, char):
    return SimpleNamespace(conn=conn, char=char,
                           member=SimpleNamespace(uid=game.uid_for(char)))


def _run(handler, session, cmd, params):
    w = Writer()
    asyncio.run(handler(session, w, cmd, [str(p) for p in params], {}))
    return w.packets


def main():
    db.use_throwaway()
    seed.run()
    conn = db.connect()
    char = game.login(conn, "__client_2609__", "pw")
    session = _session(conn, char)

    # --- getClassSkills: the preview panel blocks until this answers -----------------------
    starter = game.STARTER_CLASS_ITEM
    packets = _run(item_handlers.get_class_skills, session, "getClassSkills", [starter])
    assert len(packets) == 1 and packets[0]["Cmd"] == "classSkills"
    assert packets[0]["ItemID"] == starter, "the reply is keyed by the class-armor item id"
    skills = packets[0]["skillList"]
    assert skills, "the starter class has a skill bar"
    # same shape as sEAct.skillList, which is what the client stores it into
    assert skills == forge.build_seact(conn, forge.class_for_armor_item(conn, starter))["skillList"]

    # an armor with no mapped class still answers (empty), so the panel settles
    packets = _run(item_handlers.get_class_skills, session, "getClassSkills", [99_999_999])
    assert packets[0]["skillList"] == {}, "unmatched class armor answers with an empty bar"
    assert packets[0]["ItemID"] == 99_999_999

    # a non-numeric id is ignored rather than crashing the session
    assert _run(item_handlers.get_class_skills, session, "getClassSkills", ["nope"]) == []

    # --- getMonDrops: the panel shows authored rows PLUS the global table ------------------
    authored = conn.execute(
        "SELECT mon_id, item_id FROM monster_drops ORDER BY mon_id LIMIT 1").fetchone()
    assert authored is not None, "seed ships at least one authored monster drop"
    mon_id = int(authored["mon_id"])
    packets = _run(loot_cmds.get_mon_drops, session, "getMonDrops", [mon_id])
    assert len(packets) == 1 and packets[0]["Cmd"] == "getMonDrops"
    assert packets[0]["monID"] == mon_id
    drops = packets[0]["ItemDrops"]
    assert drops == loot.drop_table(conn, mon_id)
    ids = {d["itemID"] for d in drops}
    assert int(authored["item_id"]) in ids, "the monster's own drop is listed"
    global_ids = {int(r["item_id"]) for r in
                  conn.execute("SELECT item_id FROM global_drops").fetchall()}
    assert global_ids & ids, "the universal table every monster rolls is listed too"
    for d in drops:
        assert d["ItemData"] is not None, "the client dereferences ItemData to draw the row"
        assert d["Quantity"] <= d["QtyMax"] and 0.0 <= d["Rate"] <= 1.0
        assert d["Hide"] is False

    # a monster with nothing authored still answers — with just the global table
    packets = _run(loot_cmds.get_mon_drops, session, "getMonDrops", [987_654])
    assert packets[0]["monID"] == 987_654
    assert {d["itemID"] for d in packets[0]["ItemDrops"]} == global_ids

    # --- getRandomStatues: positional, deduplicated, echoes Gen ---------------------------
    owner = game.login(conn, "__client_2609_owner__", "pw")
    statues.generate(conn, owner, now=5000)
    conn.commit()
    owner_session = _session(conn, owner)

    # "me" resolves to the viewer's own statue; "0" picks any; a specific cid is honoured
    packets = _run(player_handlers.get_random_statues, owner_session,
                   "getRandomStatues", [7, 3, "me", "0", owner["id"]])
    assert len(packets) == 1 and packets[0]["Cmd"] == "getRandomStatues"
    assert packets[0]["Gen"] == 7, "the client drops a reply whose Gen is not in flight"
    cids, names = packets[0]["Cids"], packets[0]["Names"]
    assert len(cids) == 3 and len(names) == 3, "the reply is matched positionally per pedestal"
    assert cids[0] == owner["id"] and names[0] == owner["name"]
    # owner is the only statue on the server and pedestal 0 took it, so a RANDOM pedestal has
    # nothing left to show
    assert cids[1] == 0 and names[1] == ""
    # ...but a pedestal pinned to a specific cid still shows it. The client dedupes only its
    # random picks (PickFromList skips assigned cids; FirstId/SpecificStatueID does not), so a
    # pedestal authored to display one particular hero must not be starved by that rule.
    assert cids[2] == owner["id"] and names[2] == owner["name"]

    # a viewer with no statue of their own gets the fallback for "me"
    packets = _run(player_handlers.get_random_statues, session,
                   "getRandomStatues", [1, 1, "me"])
    assert packets[0]["Cids"] == [0] and packets[0]["Names"] == [""]

    # an excluded cid (already on screen) is not handed out again
    packets = _run(player_handlers.get_random_statues, session,
                   "getRandomStatues", [2, 1, "0", owner["id"]])
    assert packets[0]["Cids"] == [0], "exclude keeps a statue off a second pedestal"

    # without the exclusion the same request does fill
    packets = _run(player_handlers.get_random_statues, session,
                   "getRandomStatues", [3, 1, "0"])
    assert packets[0]["Cids"] == [owner["id"]]

    # a batch larger than the client's own cap is clamped, never unbounded
    over = statues.MAX_RANDOM_STATUES + 5
    packets = _run(player_handlers.get_random_statues, session,
                   "getRandomStatues", [4, over] + ["0"] * over)
    assert len(packets[0]["Cids"]) == statues.MAX_RANDOM_STATUES

    # a malformed batch is ignored rather than answered with a mismatched length
    assert _run(player_handlers.get_random_statues, session, "getRandomStatues", [5]) == []

    _traits(conn, char)
    _movement()
    _warnings(conn)

    print("ALL 2026-09-17 CLIENT CONTRACT TESTS PASSED")


def _movement():
    """monWalk / monLeash: chase while engaged, walk home when not."""
    import monstermove

    area = "movetest-1"
    monstermove.register_area(area, [
        {"MonMapID": 1, "MonID": 10, "strMonName": "Runner", "x": 0.0, "y": 0.0},
        {"MonMapID": 2, "MonID": 11, "strMonName": "Statue", "x": 5.0, "y": 0.0,
         "NoMove": True},
    ])
    runner, statue = "m:1", "m:2"
    assert monstermove.position(area, runner) == (0.0, 0.0)
    assert monstermove.can_move(area, runner)
    assert not monstermove.can_move(area, statue), "a NoMove placement is scenery"

    # scenery never walks, however close the player gets
    assert monstermove.chase(area, statue, 40.0, 0.0, now=100.0) is None

    # a player well out of range pulls the monster toward them, stopping at melee range
    walk = monstermove.chase(area, runner, 10.0, 0.0, now=100.0)
    assert walk is not None and walk["Cmd"] == "monWalk"
    assert walk["monMapID"] == 1, "the packet carries the map instance id, not the catalog id"
    assert abs(walk["x"] - (10.0 - monstermove.STOP_RANGE)) < 0.01 and walk["y"] == 0.0
    assert walk["speed"] == monstermove.WALK_SPEED
    assert monstermove.displaced(area, runner)

    # the rate limit keeps one monster to one packet per interval
    assert monstermove.chase(area, runner, 12.0, 0.0, now=100.1) is None
    assert monstermove.chase(area, runner, 12.0, 0.0,
                             now=100.0 + monstermove.CHASE_INTERVAL + 0.01) is not None

    # already in range -> nothing to send
    here = monstermove.position(area, runner)
    assert monstermove.chase(area, runner, here[0], here[1], now=200.0) is None

    # a player kiting past the leash is not followed
    assert monstermove.chase(area, runner, 500.0, 0.0, now=300.0) is None, \
        "a destination past the leash means give up, not chase forever"

    # still engaged -> no leash
    assert monstermove.leash_idle({(area, runner)}) == []

    # disengaged -> walks home exactly once, and is home afterwards
    idle = monstermove.leash_idle(set())
    assert [(a, p["Cmd"], p["monMapID"]) for a, p in idle] == [(area, "monLeash", 1)]
    assert monstermove.position(area, runner) == monstermove.spawn_point(area, runner)
    assert not monstermove.displaced(area, runner)
    assert monstermove.leash_idle(set()) == [], "a monster already home is not leashed again"

    # re-registering a room resets displacement (a fresh instance starts on its spawns)
    assert monstermove.chase(area, runner, 10.0, 0.0, now=400.0) is not None
    assert monstermove.displaced(area, runner)
    monstermove.register_area(area, [{"MonMapID": 1, "MonID": 10, "x": 0.0, "y": 0.0}])
    assert not monstermove.displaced(area, runner)

    monstermove.forget_area(area)
    assert monstermove.position(area, runner) is None


def _warnings(conn):
    """dataWarnings: a placement pointing at content that does not exist is reported."""
    import datawarnings

    good_mon = conn.execute("SELECT mon_id FROM monsters ORDER BY mon_id LIMIT 1").fetchone()
    good_apop = conn.execute("SELECT apop_id FROM apops ORDER BY apop_id LIMIT 1").fetchone()

    clean = {"monBranch": [{"MonMapID": 1, "MonID": int(good_mon["mon_id"]),
                            "strMonName": "Fine", "apopID": int(good_apop["apop_id"])}]}
    assert datawarnings.for_area(conn, "battleon", clean, requested_map="battleon") == []
    assert datawarnings.packet([]) is None, "an empty warning set is not worth a packet"

    broken = {"monBranch": [
        {"MonMapID": 7, "MonID": 999_999, "strMonName": "Ghost", "apopID": -1},
        {"MonMapID": 8, "MonID": int(good_mon["mon_id"]), "strMonName": "Mute",
         "apopID": 888_888},
    ]}
    warns = datawarnings.for_area(conn, "battleon", broken, requested_map="battleon")
    assert len(warns) == 2
    assert any("Ghost" in w and "999999" in w and "catalog row" in w for w in warns)
    assert any("Mute" in w and "888888" in w and "apop" in w for w in warns)

    # a map we have no data for reports the substitution it made
    fallback = datawarnings.for_area(conn, "infinityportal", {"monBranch": []},
                                     requested_map="nosuchmap")
    assert len(fallback) == 1 and "nosuchmap" in fallback[0] and "infinityportal" in fallback[0]

    pkt = datawarnings.packet(warns)
    assert pkt["Cmd"] == "dataWarnings" and pkt["warnings"] == warns


TRAIT_SHOP = 9_100_001
TRAIT_ONE = 990_045          # a 1-slot trait
TRAIT_BIG = 990_046          # a 3-slot trait: fills the whole default budget
NOT_A_TRAIT = 990_047


def _stock(conn, shop_item_id, item_id, name, item_type, slot_cost):
    """Author one trait-shop listing. No trait content ships yet, so the test mints its own."""
    db.store_item(conn, {"ID": item_id, "Name": name, "ItemType": item_type,
                         "Description": name + " description", "Level": 7}, replace=True)
    conn.execute("DELETE FROM shop_items WHERE shop_id=? AND shop_item_id=?",
                 (TRAIT_SHOP, shop_item_id))
    conn.execute("INSERT INTO shop_items(shop_id, shop_item_id, item_id, cost, coins, "
                 "quantity_remain) VALUES(?,?,?,?,0,-1)",
                 (TRAIT_SHOP, shop_item_id, item_id, slot_cost))
    conn.commit()


def _traits(conn, char):
    """applyTrait / removeTrait against both targets the client can send: a loose bag gem and a
    gem already sitting on gear."""
    import patterns
    from handlers import patterns_cmds

    _stock(conn, 1, TRAIT_ONE, "Keen", patterns.TRAIT_ITEM_TYPE, 1)
    _stock(conn, 2, TRAIT_BIG, "Ruinous", patterns.TRAIT_ITEM_TYPE, 3)
    _stock(conn, 3, NOT_A_TRAIT, "Just A Sword", 6, 1)

    trait_char = game.login(conn, "__client_2609_traits__", "pw")
    session = _session(conn, trait_char)

    # --- a loose bag gem ------------------------------------------------------------------
    gear_id = game.STARTER_WEAPON_ITEM          # Default Sword: every character starts with it
    gem = patterns.roll_pattern(db.item(conn, gear_id))
    assert gem is not None, "a weapon mints a rollable gem"
    cpid = patterns.grant_gem(conn, trait_char["id"], gem)
    conn.commit()

    packets = _run(patterns_cmds.apply_trait, session, "applyTrait", [TRAIT_SHOP, 1, -1, cpid])
    assert len(packets) == 1 and packets[0]["Cmd"] == "addItems", \
        "a bag gem has no UpdatePattern path, so it refreshes through addItems.patternItems"
    pat = packets[0]["patternItems"][0]["pattern"]
    assert packets[0]["patternItems"][0]["CharPatternID"] == cpid
    assert [t["ID"] for t in pat["Traits"]] == [TRAIT_ONE]
    assert pat["Traits"][0]["Name"] == "Keen" and pat["Traits"][0]["Cost"] == 1
    assert pat["SlotsUsed"] == 1 and pat["MaxSlots"] == patterns.DEFAULT_TRAIT_SLOTS
    stored = patterns._parse_pattern(conn.execute(
        "SELECT pattern_json FROM char_patterns WHERE char_pattern_id=?", (cpid,)
    ).fetchone()["pattern_json"])
    assert patterns.slots_used(stored) == 1, "the slot spend is persisted, not just echoed"

    # the same trait twice is refused even though the client already guards it
    packets = _run(patterns_cmds.apply_trait, session, "applyTrait", [TRAIT_SHOP, 1, -1, cpid])
    assert packets[0]["Cmd"] == "UpdatePattern" and packets[0]["Success"] is False
    assert "already has this trait" in packets[0]["errorMessage"]

    # and so is one that does not fit the remaining budget (1 used, 3 needed of 3)
    packets = _run(patterns_cmds.apply_trait, session, "applyTrait", [TRAIT_SHOP, 2, -1, cpid])
    assert packets[0]["Success"] is False and "open slots" in packets[0]["errorMessage"]
    assert patterns.slots_used(stored) == 1

    # a shop listing that is not a trait item cannot be slotted into a gem
    packets = _run(patterns_cmds.apply_trait, session, "applyTrait", [TRAIT_SHOP, 3, -1, cpid])
    assert packets[0]["Success"] is False and "not a trait" in packets[0]["errorMessage"]

    # a listing this shop does not sell is refused (a crafted ShopItemID)
    packets = _run(patterns_cmds.apply_trait, session, "applyTrait", [TRAIT_SHOP, 99, -1, cpid])
    assert packets[0]["Success"] is False and "not sold here" in packets[0]["errorMessage"]

    # removing gives the slots back
    packets = _run(patterns_cmds.remove_trait, session, "removeTrait", [TRAIT_ONE, -1, cpid])
    assert packets[0]["Cmd"] == "addItems"
    freed = packets[0]["patternItems"][0]["pattern"]
    assert freed["Traits"] == [] and freed["SlotsUsed"] == 0

    # removing a trait that is not on the gem is refused
    packets = _run(patterns_cmds.remove_trait, session, "removeTrait", [TRAIT_ONE, -1, cpid])
    assert packets[0]["Success"] is False and "not on this gem" in packets[0]["errorMessage"]

    # the 3-slot trait now fits, and fills the budget exactly
    packets = _run(patterns_cmds.apply_trait, session, "applyTrait", [TRAIT_SHOP, 2, -1, cpid])
    assert packets[0]["Cmd"] == "addItems"
    assert packets[0]["patternItems"][0]["pattern"]["SlotsUsed"] == 3

    # --- a gem already applied to gear ----------------------------------------------------
    game.give_item(conn, trait_char, gear_id, 1)
    applied = patterns.item_default_pattern(conn, trait_char["id"], gear_id)
    assert applied["Success"], "the gear carries a default gem to put a trait on"
    conn.commit()

    packets = _run(patterns_cmds.apply_trait, session, "applyTrait",
                   [TRAIT_SHOP, 1, gear_id, -1])
    assert len(packets) == 1 and packets[0]["Cmd"] == "UpdatePattern", \
        "a gear gem rides UpdatePattern so the client can resolve it by item id"
    assert packets[0]["Success"] is True and packets[0]["ItemID"] == gear_id
    assert packets[0]["silent"] is True, "the panel closed itself; do not re-open a preview"
    assert [t["ID"] for t in packets[0]["pattern"]["Traits"]] == [TRAIT_ONE]

    packets = _run(patterns_cmds.remove_trait, session, "removeTrait",
                   [TRAIT_ONE, gear_id, -1])
    assert packets[0]["Success"] is True and packets[0]["pattern"]["Traits"] == []

    # an unknown gem is reported, not silently dropped
    packets = _run(patterns_cmds.apply_trait, session, "applyTrait",
                   [TRAIT_SHOP, 1, -1, 7_654_321])
    assert packets[0]["Success"] is False and "Gem not found" in packets[0]["errorMessage"]


if __name__ == "__main__":
    main()
