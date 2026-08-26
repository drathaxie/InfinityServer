"""Focused Mage class regressions: targeting, combos, control, shield, and VFX."""
import json
import random
import time
from pathlib import Path
from unittest.mock import patch

import combat
import seed


ROOT = Path(__file__).resolve().parent.parent
GRAPHS = json.loads((ROOT / "data" / "skill_graphs.json").read_text(encoding="utf-8"))


def reset():
    for state in (
        combat._mon, combat._maxhp, combat._moninfo, combat._php, combat._pmax,
        combat._power, combat._rp, combat._resource_model, combat._class_mana,
        combat._rp_max, combat._casts, combat._auras, combat._stun, combat._aggro,
        combat._auto, combat._cast_last,
    ):
        state.clear()


def graph(skill_id):
    source = GRAPHS[str(skill_id)]
    data = source["data"]
    if skill_id in seed.SKILL_DAMAGE:
        data = seed._author_damage(data, *seed.SKILL_DAMAGE[skill_id])
    data = seed._author_mage_fx(data, skill_id)
    return data, source["forge"]


def setup(uid, area, mana=100, intellect=40, monsters=("m:1",)):
    combat.register_player(uid, 1000)
    combat.set_resource_model(uid, "mana", 100)
    combat.set_class_mana(uid, {135: 0, 136: 15, 137: 15, 138: 10, 139: 0})
    combat.set_power(uid, {"ap": 100, "sp": 100, "tcr": 0.01, "scm": 1.5,
                           "tha": 1.0, "INT": intellect}, weapon=(100, 100))
    combat._power[uid]["tcr"] = 0.0
    combat._rp[uid] = mana
    for target in monsters:
        combat.register_monster(area, target, 10000)


def stream(uid, area, slot, skill_id, target="m:1", returned=None):
    data, forge_data = graph(skill_id)
    packets, killed, damage = combat.begin_cast(
        area, uid, slot, target, data, forge_data, skill_id)
    assert packets[-1]["Cmd"] == "igai"
    request = packets[-1]
    ctx = request["ContextId"]
    returned = [target] if returned is None else list(returned)
    packets, killed, damage = combat.resume_cast(
        ctx, [str(slot), ctx, request["Response"]["Name"], *returned])
    return packets, killed, damage


def damage_node(packets):
    return next(node for node in packets[0]["Nodes"] if node.get("Name") == "Damage")


def test_magic_missile_low_mana_bonus():
    reset()
    setup(1001, "missile", mana=20)
    total, empowered = combat._apply_determination(1001, 0, combat.MAGE_MAGIC_MISSILE)
    assert total == 40 and not empowered, "Magic Missile restores 20 below 30 mana"
    combat._rp[1001] = 30
    total, _ = combat._apply_determination(1001, 0, combat.MAGE_MAGIC_MISSILE)
    assert total == 40, "the doubled restoration ends at 30 mana"


def test_fireball_consumes_frozen_blood_for_double_damage():
    reset()
    setup(1002, "fire-base")
    random.seed(7)
    base_packets, _, base_damage = stream(1002, "fire-base", 1, combat.MAGE_FIREBALL)
    assert combat.aura_active("fire-base", "m:1", "Scorched")

    setup(1003, "fire-frozen")
    combat.apply_aura("fire-frozen", "Frozen Blood", ["m:1"], "p:1003")
    random.seed(7)
    packets, _, damage = stream(1003, "fire-frozen", 1, combat.MAGE_FIREBALL)
    assert damage == base_damage * 2
    assert not combat.aura_active("fire-frozen", "m:1", "Frozen Blood")
    assert combat.aura_active("fire-frozen", "m:1", "Scorched")
    assert any(p.get("Cmd") == "AuraChange" and p.get("nam") == "Frozen Blood"
               for p in packets[1:])
    assert damage_node(base_packets)["Targets"] == ["m:1"]


def test_ice_shard_consumes_scorched_and_stuns_once_per_life():
    reset()
    setup(1004, "ice")
    combat.apply_aura("ice", "Scorched", ["m:1"], "p:1004")
    packets, _, _ = stream(1004, "ice", 2, combat.MAGE_ICE_SHARD)
    assert not combat.aura_active("ice", "m:1", "Scorched")
    assert combat.aura_active("ice", "m:1", "Frozen Blood")
    assert combat.aura_active("ice", "m:1", "Freeze Immune")
    assert combat.is_stunned("ice", "m:1")
    assert combat.monster_speed_multiplier("ice", "m:1") == 0.0
    assert any(p.get("nam") == "Scorched" for p in packets[1:])

    combat._auras[("ice", "m:1")]["Stunned"]["ends"] = time.time() - 1
    combat._stun[("ice", "m:1")] = time.time() - 1
    packets, _, _ = stream(1004, "ice", 2, combat.MAGE_ICE_SHARD)
    auras = [n.get("AuraName") for n in packets[0]["Nodes"] if n.get("Name") == "Aura"]
    assert "Stunned" not in auras, "Freeze Immune suppresses repeat Ice Shard stuns"
    assert combat.monster_speed_multiplier("ice", "m:1") == 0.90

    combat.death_packets("ice", "m:1", 1004)
    assert not combat.aura_active("ice", "m:1", "Freeze Immune"), \
        "the first-use stun resets on a new monster life"


def test_explosion_is_hostile_aoe_and_resolves_both_combos():
    reset()
    targets = tuple(f"m:{i}" for i in range(1, 7))
    setup(1005, "explosion", monsters=targets)
    combat.apply_aura("explosion", "Scorched", ["m:1"], "p:1005")
    combat.apply_aura("explosion", "Frozen Blood", ["m:1"], "p:1005")
    packets, _, damage = stream(
        1005, "explosion", 3, combat.MAGE_EXPLOSION,
        returned=["p:1005", "m:2", "m:3", "m:4", "m:5", "m:6"],
    )
    node = damage_node(packets)
    assert node["Targets"] == ["m:1", "m:2", "m:3", "m:4"]
    assert damage > 0 and all(t.startswith("m:") for t in node["Targets"])
    assert not combat.aura_active("explosion", "m:1", "Scorched")
    assert not combat.aura_active("explosion", "m:1", "Frozen Blood")
    assert combat.aura_active("explosion", "m:1", "Shattered")
    assert combat.outgoing_damage_multiplier("explosion", "m:1") == 0.90
    assert (combat._auras[("explosion", "m:1")]["Detonation"]["ticks_left"] == 2)
    removed = {p.get("nam") for p in packets[1:] if p.get("Cmd") == "AuraChange"}
    assert {"Scorched", "Frozen Blood"}.issubset(removed)

    detonation = combat._auras[("explosion", "m:1")]["Detonation"]
    detonation["next"] = 0
    first = combat.aura_ticks()
    detonation["next"] = 0
    second = combat.aura_ticks()
    ticks = [n for rows in (first, second) for _area, packet, _killed in rows
             for n in packet.get("Nodes", []) if n.get("Targets") == ["m:1"]]
    assert len(ticks) == 2 and all(n["DamageTypes"] == [combat.DT_DOT] for n in ticks)


def test_arcane_shield_scales_reduces_and_spends_mana():
    reset()
    setup(1006, "shield", mana=50, intellect=100)
    data, forge_data = graph(combat.MAGE_ARCANE_SHIELD)
    attack, _, _ = combat.cast_skill(
        "shield", 1006, 4, "m:1", data, forge_data, combat.MAGE_ARCANE_SHIELD,
        allies=["p:1006", "p:2000"],
    )
    aura = next(n for n in attack["Nodes"]
                if n.get("Name") == "Aura" and n.get("AuraName") == "Arcane Shield")
    assert aura["Targets"] == ["p:1006"], "Arcane Shield is self-only"
    assert combat._guard_reduction("shield", "p:1006") == 0.25

    with patch.object(combat.random, "random", return_value=1.0), \
            patch.object(combat.random, "randint", return_value=100):
        monster_attack, hp, _ = combat.monster_attack("shield", "m:1", 1006)
        assert monster_attack["Nodes"][0]["Damages"] == [75]
        assert hp == 925 and combat.resource_total(1006) == 45
        for _ in range(9):
            combat.monster_attack("shield", "m:1", 1006)
    assert combat.resource_total(1006) == 0
    assert not combat.aura_active("shield", "p:1006", "Arcane Shield")
    removes = [packet for _area, packet, _killed in combat.aura_ticks()
               if packet.get("Cmd") == "AuraChange"]
    assert any(p.get("nam") == "Arcane Shield" for p in removes)


def test_mage_spell_graphs_restore_shipped_projectiles():
    for skill_id, expected in seed.MAGE_SPELL_FX.items():
        data, _ = graph(skill_id)
        spell = next(node for node in data[1].values()
                     if node.get("Name") == "SpellAnimation")
        assert (spell["SpellGraphic"], spell["SpellImpact"]) == expected
        assert spell["Follow"] is True and spell["AttachImpact"] == "Origin"
    custom = json.loads(json.dumps(GRAPHS["136"]["data"]))
    spell = next(node for node in custom[1].values() if node.get("Name") == "SpellAnimation")
    spell["SpellGraphic"] = "custom_fireball_projectile"
    patched = seed._author_mage_fx(custom, 136)
    patched_spell = next(node for node in patched[1].values()
                         if node.get("Name") == "SpellAnimation")
    assert patched_spell["SpellGraphic"] == "custom_fireball_projectile", \
        "the additive migration must preserve non-empty Forge customization"


def main():
    test_magic_missile_low_mana_bonus()
    test_fireball_consumes_frozen_blood_for_double_damage()
    test_ice_shard_consumes_scorched_and_stuns_once_per_life()
    test_explosion_is_hostile_aoe_and_resolves_both_combos()
    test_arcane_shield_scales_reduces_and_spends_mana()
    test_mage_spell_graphs_restore_shipped_projectiles()
    print("MAGE COMBAT TESTS PASSED: mana, combos, AoE, control, shield, projectiles")


if __name__ == "__main__":
    main()
