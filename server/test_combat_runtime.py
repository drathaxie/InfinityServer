"""Live combat-loop regressions that packet-replay tests cannot catch.

These checks cover the boundary between the network handler, the sustained
auto loop, resources, engagement state, and monster presentation.  Run with
``python test_combat_runtime.py`` or pytest.
"""
import asyncio
import json
from unittest.mock import patch

import combat
from handlers import combat_cmds


AREA = "runtime-audit"
UID = 8801
SKILL_ID = 99001
TARGET = "m:91"

DATA = [
    {"0": {"Name": "OnRequest"}},
    {
        "1": {"Name": "PlayerAnimation", "Animation": "Attack1"},
        "2": {"Name": "Damage", "DamageType": "Physical", "Multiplier": 1.0},
        "3": {"Name": "Cooldown", "CD": 1200},
    },
]
FORGE = [{}, {"0": {"Next": {"id": "1", "Next": {"id": "2", "Next": {"id": "3"}}}}}]
INPUT_DATA = [
    {"0": {"Name": "OnRequest"}},
    {
        "1": {"Name": "Range", "HRange": 5, "VRange": 1},
        "2": {"Name": "Damage", "DamageType": "Physical", "Multiplier": 1.0},
        "3": {"Name": "Cooldown", "CD": 1200},
    },
]
INPUT_FORGE = [{}, {"0": {"Next": {"id": "1", "Next": {"id": "2", "Next": {"id": "3"}}}}}]


class Writer:
    def __init__(self):
        self.data = b""

    def write(self, data):
        self.data += data

    async def drain(self):
        pass

    def packets(self):
        if not self.data:
            return []
        return [json.loads(p) for p in self.data.rstrip(b"\0").split(b"\0")]


class Member:
    uid = UID
    name = "Runtime Auditor"
    frame = "Enter"


class Session:
    member = Member()
    char = {"id": 1, "name": Member.name, "access_level": 0}
    conn = object()
    equipped_class = 1
    area = AREA


def reset_world(model="determination", max_rp=100):
    for d in (combat._mon, combat._maxhp, combat._moninfo, combat._last, combat._aggro,
              combat._php, combat._pmax, combat._power, combat._rp,
              combat._resource_model, combat._class_mana, combat._rp_max,
              combat._class_rules, combat._cast_last, combat._auto):
        d.clear()
    combat._casts.clear()
    combat._delayed.clear()
    combat.register_player(UID, 2000)
    combat.set_power(UID, {"ap": 50, "sp": 50, "tcr": 0, "scm": 1.5, "tha": 1})
    combat.set_resource_model(UID, model, max_rp)
    combat.register_monster(AREA, TARGET, 10000, mon_id=91, frame="Enter", level=5)


def skill():
    return {"skill_id": SKILL_ID, "name": "Runtime Strike", "data": DATA, "forge": FORGE}


def input_skill():
    return {"skill_id": SKILL_ID, "name": "Runtime Range Strike",
            "data": INPUT_DATA, "forge": INPUT_FORGE}


def test_cast_admission_and_mana():
    reset_world("mana", 40)
    combat.set_class_mana(UID, {SKILL_ID: 25})
    session = Session()

    # A targetless class skill picks the living monster in the current cell.
    writer = Writer()
    with patch.object(combat_cmds.forge, "skill_for_slot", return_value=skill()):
        asyncio.run(combat_cmds.begin_cast(session, writer, "gar", ["1", ""], {}))
    packets = writer.packets()
    assert packets[0]["Cmd"] == "Attack" and packets[0]["StatusCode"] == 1
    assert packets[-1]["Cmd"] == "hpmp" and packets[-1]["RP"] == 15
    assert combat.resource_total(UID) == 15
    assert (UID, 1) in combat._cast_last
    autos = combat.auto_engagements()
    assert len(autos) == 1 and autos[0][0:3] == (UID, AREA, TARGET), \
        "a skill-started fight must register the sustained class auto"
    assert autos[0][-1] == SKILL_ID and (UID, 0) in combat._cast_last, \
        "the repeat keeps the equipped auto skill and starts on its authored cadence"
    combat.release_cooldown(UID, 0)
    _auto_uid, auto_area, auto_target, auto_data, auto_forge, _auto_cd, auto_skill_id = autos[0]
    auto_packets, _auto_killed, _auto_damage = combat.execute_auto(
        auto_area, UID, auto_target, auto_data, auto_forge, auto_skill_id)
    assert auto_packets and auto_packets[0]["Cmd"] == "Attack" \
        and auto_packets[0]["StatusCode"] == 1, \
        "the auto registered by the opening skill must execute through the repeat path"

    # Most legacy class skills first return an igai Range/Hitbox handshake, not
    # an immediate successful Attack. Acceptance of that handshake still starts
    # the slot-0 loop (Warrior/Dragonslayer/Healer regression).
    reset_world("mana", 40)
    combat.set_class_mana(UID, {SKILL_ID: 25})
    writer = Writer()
    with patch.object(combat_cmds.forge, "skill_for_slot",
                      side_effect=lambda _conn, _class, slot: skill() if slot == 0
                      else input_skill()):
        asyncio.run(combat_cmds.begin_cast(session, writer, "gar", ["1", TARGET], {}))
    packets = writer.packets()
    assert packets[0]["Cmd"] == "igai" and not any(
        pk.get("Cmd") == "Attack" and pk.get("StatusCode") == 1 for pk in packets)
    autos = combat.auto_engagements()
    assert len(autos) == 1 and autos[0][0:3] == (UID, AREA, TARGET), \
        "an accepted Range/Hitbox handshake must start the sustained class auto"

    # An empty current cell still fails and releases the pending request.
    reset_world("mana", 40)
    combat.set_class_mana(UID, {SKILL_ID: 25})
    combat._mon[(AREA, TARGET)] = 0
    writer = Writer()
    with patch.object(combat_cmds.forge, "skill_for_slot", return_value=skill()):
        asyncio.run(combat_cmds.begin_cast(session, writer, "gar", ["1", ""], {}))
    packets = writer.packets()
    assert len(packets) == 1 and packets[0]["StatusCode"] == 0
    assert "target" in packets[0]["Error"].lower()
    assert combat.resource_total(UID) == 40
    assert (UID, 1) not in combat._cast_last
    assert not combat.auto_engagements(), "a failed targetless cast must not start autos"

    # Insufficient mana is a server-side rejection, not a cast floored to zero.
    reset_world("mana", 40)
    combat.set_class_mana(UID, {SKILL_ID: 25})
    combat._rp[UID] = 10
    writer = Writer()
    with patch.object(combat_cmds.forge, "skill_for_slot", return_value=skill()):
        asyncio.run(combat_cmds.begin_cast(session, writer, "gar", ["1", TARGET], {}))
    packets = writer.packets()
    assert len(packets) == 1 and packets[0]["StatusCode"] == 0
    assert packets[0]["Error"] == "Not enough Mana!,25"
    assert combat.resource_total(UID) == 10
    assert (UID, 1) not in combat._cast_last
    assert not combat.auto_engagements(), "an unaffordable cast must not start autos"
    assert combat._mon[(AREA, TARGET)] == 10000

    # A valid cast spends exactly once and sends an authoritative hpmp sync.
    combat._rp[UID] = 30
    writer = Writer()
    with patch.object(combat_cmds.forge, "skill_for_slot", return_value=skill()):
        asyncio.run(combat_cmds.begin_cast(session, writer, "gar", ["1", TARGET], {}))
    packets = writer.packets()
    assert packets[0]["Cmd"] == "Attack" and packets[0]["StatusCode"] == 1
    assert packets[-1]["Cmd"] == "hpmp" and packets[-1]["RP"] == 5
    assert combat.resource_total(UID) == 5
    assert combat.in_combat(UID, AREA)

    # Rest is not an in-combat full-heal exploit.
    combat._php[UID] = 100
    writer = Writer()
    asyncio.run(combat_cmds.rest_revive(session, writer, "rest", [], {}))
    assert writer.packets() == [
        {"Cmd": "rNotify", "msg": "You cannot rest while in combat."}
    ]
    assert combat.player_hp(UID) == 100


def test_sustained_auto_uses_data_rules_and_refreshes_aggro():
    reset_world("stacking", 12)
    rules = {
        "resource": {"model": "stacking", "max": 12},
        "skills": {str(SKILL_ID): [
            {"Do": "ResourceOp", "Op": "gain", "Amount": 1},
            {"Do": "Graph"},
        ]},
    }
    combat.set_class_rules(UID, rules)
    combat.auto_engage(UID, AREA, TARGET, DATA, FORGE, 1200, SKILL_ID)
    before_lease = combat._aggro[(AREA, TARGET)]["last"]

    packets, killed, damage = combat.execute_auto(
        AREA, UID, TARGET, DATA, FORGE, SKILL_ID, [f"p:{UID}"])
    assert packets and packets[0]["Cmd"] == "Attack" and damage > 0 and not killed
    assert combat.resource_total(UID) == 1, "repeat auto must run the class rule graph"
    assert combat._aggro[(AREA, TARGET)]["last"] >= before_lease
    auto = combat.auto_engagements()[0]
    assert auto[-1] == SKILL_ID, "the repeat loop must retain the authored auto skill id"


def test_monster_attack_matches_captured_presentation():
    reset_world()
    for _ in range(100):
        attack, hp, died = combat.monster_attack(AREA, TARGET, UID)
        if attack["Nodes"][0]["Damages"][0] > 0:
            break
    assert not died and hp < 2000
    assert [n["Name"] for n in attack["Nodes"]] == [
        "Damage", "PlayerAnimation", "Cooldown"
    ]
    animation = attack["Nodes"][1]
    assert animation == {
        "Name": "PlayerAnimation", "Animation": "Attack1,Attack2,Attack3",
        "Priority": "Low", "Speed": 1.0, "Targets": 1,
    }
    cooldown = attack["Nodes"][2]
    assert cooldown["Slot"] == 0 and cooldown["CD"] == int(combat.MON_ATTACK_CD * 1000)


def test_pure_data_class_fails_closed():
    reset_world("stacking", 12)
    broken = {"resource": {"model": "stacking", "max": 12},
              "skills": {str(SKILL_ID): [{"Do": "NoSuchRule"}]}}
    combat.set_class_rules(UID, broken)
    packets, killed, damage = combat.cast_skill_rules(
        AREA, UID, 1, TARGET, DATA, FORGE, SKILL_ID, broken)
    assert not killed and damage == 0 and packets[0]["StatusCode"] == 0
    assert combat.resource_total(UID) == 0
    assert combat.class_rules(UID, SKILL_ID) is broken, \
        "a pure-data class must not be switched to incompatible Determination fallback"


def test_class_cap_is_used_by_mana_rest():
    reset_world("mana", 40)
    combat._rp[UID] = 3
    packet = combat.rest_player(UID, Member.name)
    assert combat.resource_total(UID) == 40 and packet["RP"] == 40


def test_spellstone_does_not_mutate_class_resource():
    reset_world("determination", 100)
    combat._rp[UID] = 20
    attack, killed, damage = combat.cast_skill(
        AREA, UID, 5, None, DATA, FORGE, SKILL_ID, [f"p:{UID}"])
    assert attack["Cmd"] == "Attack" and not killed and damage == 0
    assert combat.resource_total(UID) == 20


def test_lifecycle_cancels_old_combat_context():
    reset_world()
    input_data = [{"0": {"Name": "OnRequest"}},
                  {"1": {"Name": "Range", "HRange": 5, "VRange": 1},
                   "2": {"Name": "Damage", "Multiplier": 1.0}}]
    input_forge = [{}, {"0": {"Next": {"id": "1", "Next": {"id": "2"}}}}]
    packets, _, _ = combat.begin_cast(AREA, UID, 1, TARGET, input_data, input_forge, SKILL_ID)
    assert packets[-1]["Cmd"] == "igai" and combat._casts
    combat.auto_engage(UID, AREA, TARGET, DATA, FORGE, 1200, SKILL_ID)
    combat.queue_delayed(AREA, [(1000, 1, (f"p:{UID}", 1, []))])
    combat._cast_last[(UID, 1)] = 123.0

    combat.cancel_player_actions(UID, clear_cooldowns=True)
    assert not combat._casts and not combat._auto and not combat._aggro
    assert not combat._delayed and (UID, 1) not in combat._cast_last


def test_cancelled_input_cast_spends_nothing():
    reset_world("mana", 40)
    combat.set_class_mana(UID, {SKILL_ID: 25})
    combat._rp[UID] = 30
    input_data = [{"0": {"Name": "OnRequest"}},
                  {"1": {"Name": "Range", "HRange": 5, "VRange": 1},
                   "2": {"Name": "Damage", "Multiplier": 1.0}}]
    input_forge = [{}, {"0": {"Next": {"id": "1", "Next": {"id": "2"}}}}]
    assert combat.off_cooldown(UID, 1, 5000)
    packets, _, _ = combat.begin_cast(
        AREA, UID, 1, TARGET, input_data, input_forge, SKILL_ID)
    ctx = packets[-1]["ContextId"]
    failed, killed, damage = combat.resume_cast(
        ctx, ["1", ctx, "Range", "validate", "false"])
    assert failed[0]["StatusCode"] == 0 and not killed and damage == 0
    assert combat.resource_total(UID) == 30
    assert (UID, 1) not in combat._cast_last, "cancelled range should not consume cooldown"


def main():
    test_cast_admission_and_mana()
    test_sustained_auto_uses_data_rules_and_refreshes_aggro()
    test_monster_attack_matches_captured_presentation()
    test_pure_data_class_fails_closed()
    test_class_cap_is_used_by_mana_rest()
    test_spellstone_does_not_mutate_class_resource()
    test_lifecycle_cancels_old_combat_context()
    test_cancelled_input_cast_spends_nothing()
    print("COMBAT RUNTIME AUDIT PASSED: admission, mana, repeat autos, aggro, monster animation")


if __name__ == "__main__":
    main()
