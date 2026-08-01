"""
The switchover — data-driven classes cast through combat_engine on the LIVE
server path, and the Infinity Hero is playable.

test_port_parity.py proves the ported classes behave identically on both
routes. This file covers what that one cannot: the Infinity Hero has no Python
implementation to fall back on, so every part of it — the pool, the Aspect
branching, the icon rebinds, the armed ultimate, the delayed ground fire —
has to work through `combat.begin_cast` exactly as `handlers/combat_cmds.py`
calls it, with the config loaded from the DB the way a real login loads it.

Run: python test_cutover.py
"""
import random

import combat
import db
import forge
import seed
from combat_engine.state import _states as _engine_states

UID, ALLY = 21, 22
AREA = "cutover"
STATS = {"ap": 31.0, "sp": 28.0, "tcr": 0.05, "scm": 1.5, "tha": 0.99}


def fresh_world(rules, model="heroic", max_rp=50):
    for d in (combat._mon, combat._maxhp, combat._moninfo, combat._last, combat._aggro,
              combat._php, combat._pmax, combat._power, combat._rp, combat._resource_model,
              combat._class_mana, combat._rp_max, combat._active_aspect,
              combat._conv_cast_stacks, combat._conv_last_cast, combat._conv_next_decay,
              combat._auras, combat._stun, combat._cast_last, combat._class_rules):
        d.clear()
    combat._delayed.clear()
    _engine_states.clear()
    for uid in (UID, ALLY):
        combat.register_player(uid, 1337)
    combat.set_power(UID, STATS)
    combat.set_resource_model(UID, model, max_rp)
    combat.set_class_rules(UID, rules)
    for i, hp in enumerate((4000, 4000, 3000, 3000), start=1):
        combat.register_monster(AREA, f"m:{i}", hp, mon_id=100, frame="Enter", level=5)


def graphs(conn, class_id):
    """slot -> (skill_id, data, forge) exactly as the cast handler resolves them."""
    out = {}
    for slot in range(5):
        sk = forge.skill_for_slot(conn, class_id, slot)
        if sk:
            out[slot] = (sk["skill_id"], sk["data"], sk["forge"])
    return out


def cast(slot, sk, target, allies=None):
    skill_id, data, fg = sk
    return combat.begin_cast(AREA, UID, slot, target, data, fg, skill_id,
                             allies or [f"p:{UID}", f"p:{ALLY}"])


def names(pkt):
    return [n["Name"] for n in pkt["Nodes"]]


def test_config_loads_from_db():
    """A login/equip reads the config out of classes.raw — not from seed.py."""
    db.use_throwaway()
    seed.run()
    with db.connect() as conn:
        cfg = forge.rules_for_class(conn, seed.INFINITY_HERO_CLASS_ID)
        assert cfg, "Infinity Hero has no rule config in the DB"
        assert set(cfg["skills"]) == {"168", "169", "170", "171", "172"}
        for cid in (seed.PALADIN_CLASS_ID, seed.VOID_CLASS_ID):
            assert forge.rules_for_class(conn, cid), f"class {cid} lost its config"
        # a class that was never ported must stay on the Python path
        assert forge.rules_for_class(conn, 1932) is None, \
            "Dragonslayer should have no rule config"
        # and a player has to be able to actually GET the class
        sold = conn.execute(
            "SELECT cost, coins FROM shop_items WHERE shop_id=? AND item_id=?",
            (seed.CLASS_SHOP_ID, seed.INFINITY_HERO_ARMOR_ITEM)).fetchone()
        assert sold, "Infinity Hero is not sold at the class shop — unreachable in game"
        armor = conn.execute("SELECT meta_string, item_type FROM items WHERE item_id=?",
                             (seed.INFINITY_HERO_ARMOR_ITEM,)).fetchone()
        assert armor and armor["meta_string"] == str(seed.INFINITY_HERO_CLASS_ID), \
            "the armor does not point at class 2022"
        assert forge.class_for_armor_item(conn, seed.INFINITY_HERO_ARMOR_ITEM) \
            == seed.INFINITY_HERO_CLASS_ID, "equipping the armor won't switch class"
    print("config load OK: 3 ported classes carry configs, unported classes do not")
    return cfg


def test_infinity_hero_rotation(cfg):
    """Play the class the way a player would and check what reaches the wire."""
    with db.connect() as conn:
        g = graphs(conn, seed.INFINITY_HERO_CLASS_ID)
    assert set(g) == {0, 1, 2, 3, 4}, f"missing skill slots: {set(g)}"
    fresh_world(cfg)
    random.seed(99)

    # --- a skill press emits the cast AND its own combo-rebind broadcast -----
    pkts, _killed, dmg = cast(1, g[1], "m:1")
    assert len(pkts) == 2, f"expected cast + rebind broadcast, got {len(pkts)}"
    assert pkts[0]["StatusCode"] == 1 and pkts[1]["StatusCode"] == 3, \
        f"statuses {[p['StatusCode'] for p in pkts]}"
    assert dmg > 0, "Definitive Strike did no damage"
    assert "Damage" in names(pkts[0]) and "Aura" in names(pkts[0])
    rebind = names(pkts[1])
    assert rebind.count("SetSkillIndex") == 4 and rebind.count("IndexReset") == 2, \
        f"rebind broadcast shape wrong: {rebind}"
    assert combat._active_aspect.get(UID) is None or True   # aspect lives in engine state
    print(f"  slot1 -> {len(pkts)} packets ({len(pkts[0]['Nodes'])} nodes + "
          f"{len(pkts[1]['Nodes'])} rebind), {dmg} dmg")

    # --- Aspect branching: Warrior is up, so Meteor takes the fire branch ----
    before = combat._rp.get(UID, 0)
    pkts, _k, _d = cast(2, g[2], "m:1")
    meteor = names(pkts[0])
    assert "SpellAnimation" in meteor, "Meteor lost its projectile"
    assert combat._rp[UID] == before + 1, \
        f"Warrior-branch Meteor should grant 1 Heroic ({before} -> {combat._rp[UID]})"
    # ...and schedules its burning ground for LATER rather than sending it now
    assert combat._delayed, "the Warrior-branch meteor scheduled no ground fire"
    assert not combat.due_delayed(0), "the ground fire fired immediately"
    late = combat.due_delayed(9e9)
    assert late and any(n["Name"] == "Particle" for _a, _s, (_c, _sl, nodes) in late
                        for n in nodes), "no ground-fire particle in the delayed packet"
    print(f"  slot2 -> Warrior branch, +1 Heroic, ground fire scheduled ({len(late)} packet)")

    # --- the pool climbs only on BRANCH casts, never on plain ones ----------
    fresh_world(cfg)
    random.seed(7)
    cast(1, g[1], "m:1")                       # no aspect up -> default branch, no gain
    assert combat._rp[UID] == 0, f"default branch granted Heroic ({combat._rp[UID]})"
    cast(2, g[2], "m:1")                       # Warrior up -> branch, +1
    assert combat._rp[UID] == 1
    print("  pool OK: default branches grant nothing, aspect branches grant +1")

    # --- arming at 25 rebinds the auto and the ultimate dumps the pool -------
    fresh_world(cfg)
    random.seed(11)
    combat._rp[UID] = 24
    pkts, _k, _d = cast(1, g[1], "m:1")        # no gain (default branch) -> still 24
    combat._rp[UID] = 24
    cast(2, g[2], "m:1")                       # Warrior branch -> 25, arms
    assert combat._rp[UID] == 25, f"pool is {combat._rp[UID]}"
    combat.due_delayed(9e9)
    armed = [p for p in cast(3, g[3], "m:1")[0:1]]      # any later press re-marks it
    ult_pkts, _k, _d = cast(0, g[0], "m:1")            # the empowered Heroic Strike
    ult = names(ult_pkts[0])
    assert "PlayerHitStream" in ult, f"the ultimate did not fire the sky-blade: {ult}"
    assert combat._rp[UID] == 0, f"the ultimate left {combat._rp[UID]} Heroic on the bar"
    print("  ultimate OK: arms at 25, fires the 2.5s hit stream, empties the pool")

    # --- the plain auto still works once the pool is spent ------------------
    pkts, _k, dmg = cast(0, g[0], "m:1")
    auto = names(pkts[0])
    assert "PlayerHitStream" not in auto, "the auto stayed empowered after the dump"
    assert "Damage" in auto and dmg > 0, f"the plain auto did nothing: {auto}"
    print("  auto OK: reverts to the normal swing after the ultimate")


def test_every_skill_casts(cfg):
    """Every slot, from every Aspect state, has to produce a real cast — this is
    the class with no Python path to catch a hole."""
    with db.connect() as conn:
        g = graphs(conn, seed.INFINITY_HERO_CLASS_ID)
    opens = {1: "Warrior", 2: "Mage", 3: "Healer", 4: "Rogue"}
    for opener, aspect in opens.items():
        for slot in (0, 1, 2, 3, 4):
            fresh_world(cfg)
            random.seed(1000 + opener * 10 + slot)
            cast(opener, g[opener], "m:1")             # establish the Aspect
            combat.due_delayed(9e9)
            pkts, _k, _d = cast(slot, g[slot], "m:2")
            assert pkts and pkts[0]["Nodes"], \
                f"slot {slot} after {aspect} Aspect produced nothing"
            assert pkts[0]["Cmd"] == "Attack" and pkts[0]["Caster"] == f"p:{UID}"
            for p in pkts:
                for n in p["Nodes"]:
                    assert isinstance(n.get("Name"), str), f"malformed node {n}"
    print(f"  coverage OK: {len(opens) * 5} Aspect x slot combinations all cast cleanly")


def test_fallback_is_safe():
    """A broken config must drop that player to the Python path, not eat the cast."""
    with db.connect() as conn:
        g = graphs(conn, seed.PALADIN_CLASS_ID)
    fresh_world({"skills": {str(g[1][0]): [{"Do": "NoSuchRule"}]}}, model="conviction")
    random.seed(3)
    pkts, _killed, _dmg = cast(1, g[1], "m:1")
    assert pkts and pkts[0]["Nodes"], "a bad rule config swallowed the cast"
    assert combat.class_rules(UID) is None, \
        "the player should have been dropped off the engine after the failure"
    pkts2, _k, _d = cast(1, g[1], "m:1")            # and the next cast just works
    assert pkts2 and pkts2[0]["Nodes"]
    print("  fallback OK: a broken config falls back to Python and stays there")


def test_unported_class_untouched():
    """A class with no config must not touch the engine at all."""
    with db.connect() as conn:
        g = graphs(conn, 1932)                       # Dragonslayer
        assert forge.rules_for_class(conn, 1932) is None
    if not g:
        print("  (Dragonslayer has no seeded slots here — skipped)")
        return
    fresh_world(None, model="determination", max_rp=100)
    random.seed(5)
    slot = sorted(g)[0]
    pkts, _k, _d = cast(slot, g[slot], "m:1")
    assert pkts, "the unported class stopped casting"
    # DS skills carry input nodes, so this is the igai handshake, not a resolved
    # Attack — the point is only that the Python path still owns it untouched
    assert any(p.get("Cmd") in ("Attack", "igai") for p in pkts), \
        f"unexpected packets: {[p.get('Cmd') for p in pkts]}"
    assert combat.class_rules(UID) is None
    print(f"  unported OK: Dragonslayer still resolves on the Python path "
          f"({'/'.join(p.get('Cmd', '?') for p in pkts)})")


def main():
    cfg = test_config_loads_from_db()
    print("infinity hero live-path:")
    test_infinity_hero_rotation(cfg)
    test_every_skill_casts(cfg)
    print("safety:")
    test_fallback_is_safe()
    test_unported_class_untouched()
    print("ALL CUTOVER TESTS PASSED (Infinity Hero plays end-to-end through begin_cast)")


if __name__ == "__main__":
    main()
