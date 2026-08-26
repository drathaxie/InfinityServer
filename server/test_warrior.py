"""Focused Warrior mechanics: On Guard must cool down repeatedly and reduce damage."""
import json
import time
from pathlib import Path
from unittest.mock import patch

import combat


ROOT = Path(__file__).resolve().parent.parent
GRAPH = json.loads((ROOT / "data" / "skill_graphs.json").read_text(encoding="utf-8"))["118"]
AREA = "warrior-on-guard"
UID = 8818
TARGET = "m:1"


def reset():
    for state in (
        combat._mon, combat._maxhp, combat._moninfo, combat._php, combat._pmax,
        combat._power, combat._rp, combat._resource_model, combat._class_mana,
        combat._rp_max, combat._casts, combat._auras, combat._stun, combat._aggro,
        combat._auto, combat._cast_last,
    ):
        state.clear()


def main():
    reset()
    combat.register_player(UID, 1000)
    combat.register_monster(AREA, TARGET, 10000, level=10)
    combat.set_resource_model(UID, "mana", 100)
    combat.set_class_mana(UID, {combat.WARRIOR_ON_GUARD: 15})
    combat.set_power(UID, {"ap": 100, "sp": 30, "tcr": 0.0, "scm": 1.5,
                           "tha": 1.0, "INT": 10}, weapon=(100, 100))

    attack, killed, damage = combat.cast_skill(
        AREA, UID, 4, TARGET, GRAPH["data"], GRAPH["forge"],
        combat.WARRIOR_ON_GUARD, allies=[f"p:{UID}"],
    )
    assert not killed and damage == 0
    cooldown = next(node for node in attack["Nodes"] if node["Name"] == "Cooldown")
    assert cooldown["CD"] == 14799 and cooldown["Animation"] == "", \
        "On Guard must start its cooldown immediately instead of staying pending on an animation"
    aura = next(node for node in attack["Nodes"] if node.get("AuraName") == "On Guard")
    assert aura["Targets"] == [f"p:{UID}"] and combat.aura_active(
        AREA, f"p:{UID}", "On Guard")
    assert combat._guard_reduction(AREA, f"p:{UID}") == 0.50
    assert combat.resource_total(UID) == 85

    with patch.object(combat.random, "random", return_value=1.0), \
            patch.object(combat.random, "randint", return_value=100):
        packet, hp, died = combat.monster_attack(AREA, TARGET, UID)
    assert not died and hp == 950
    assert packet["Nodes"][0]["Damages"] == [50], \
        "On Guard must halve incoming monster damage for its five-second lifetime"

    combat._auras[(AREA, f"p:{UID}")]["On Guard"]["ends"] = time.time() - 1
    removals = [packet for _area, packet, _kills in combat.aura_ticks()
                if packet.get("Cmd") == "AuraChange"]
    assert any(packet.get("nam") == "On Guard" for packet in removals)
    assert combat._guard_reduction(AREA, f"p:{UID}") == 0.0

    with patch.object(combat.time, "time", return_value=100.0):
        assert combat.off_cooldown(UID, 4, 14799)
    with patch.object(combat.time, "time", return_value=114.7):
        assert not combat.off_cooldown(UID, 4, 14799)
    with patch.object(combat.time, "time", return_value=114.8):
        assert combat.off_cooldown(UID, 4, 14799), \
            "the authoritative server gate must admit the next cast after 14.799 seconds"

    print("WARRIOR TEST PASSED: On Guard repeats after cooldown and grants 50% DR for 5s")


if __name__ == "__main__":
    main()
