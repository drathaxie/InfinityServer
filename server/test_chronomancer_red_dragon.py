"""End-to-end Chronomancer rotation against lair's real Red Dragon definition."""
import json
import pathlib

import combat
import db
import forge
import seed


ROOT = pathlib.Path(__file__).resolve().parent.parent


def run():
    lair = json.loads((ROOT / "data" / "maps" / "lair.json").read_text(encoding="utf-8"))
    dragon = next(m for m in lair["area"]["monBranch"] if m["strMonName"] == "Red Dragon")
    assert dragon["MonID"] == 207 and dragon["MonMapID"] == 1553
    assert dragon["intHPMax"] == 20000 and dragon["sRace"] == "Dragonkin"

    db.use_throwaway()
    db.init()
    with db.connect() as conn:
        seed.seed_chronomancer(conn)
        rules = json.loads(conn.execute("SELECT raw FROM classes WHERE class_id=2099")
                           .fetchone()["raw"])["rules"]
        skills = {sid: forge.skill_by_id(conn, sid) for sid in seed._CHRONO_SKILL_IDS}

        uid, area, target = 992, "lair", "m:1553"
        combat.register_player(uid, 2500)
        combat._php[uid] = 1000                         # make Rewind visibly testable
        combat.set_power(uid, {"ap": 110, "sp": 140, "tcr": 0, "scm": 1.5, "tha": 1})
        combat.set_resource_model(uid, "stacking", max_rp=12)
        combat.register_monster(area, target, dragon["intHPMax"], mon_id=dragon["MonID"],
                                frame=dragon["strFrame"], level=dragon["Level"],
                                race=dragon["sRace"], element=dragon["strElement"])

        def cast(slot, sid):
            skill = skills[sid]
            packets, killed, damage = combat.cast_skill_rules(
                area, uid, slot, target, skill["data"], skill["forge"], sid, rules)
            assert not killed, "the 20,000 HP boss should survive one test rotation"
            return packets[0], damage

        hp0 = combat._mon[(area, target)]
        _, auto_damage = cast(0, 90410)
        _, echo_damage = cast(1, 90411)
        before_heal = combat.player_hp(uid)
        rewind, _ = cast(2, 90412)
        assert combat.player_hp(uid) > before_heal
        assert any(n["Name"] == "Damage" and n["Damages"][0] < 0 for n in rewind["Nodes"])

        slowed, slow_damage = cast(3, 90413)
        slow = next(n for n in slowed["Nodes"] if n.get("AuraName") == "Time Dilation")
        assert slow["Targets"] == [target]
        assert slow["Duration"] == 6.0 and slow["AnimationSpeed"] == 0.35
        expected_slow_swing = combat.MON_ATTACK_CD / 0.35
        assert abs(combat.monster_attack_interval(area, target) - expected_slow_swing) < 0.01
        combat.arm_monster_skill(area, target, 100.0, 6.0, 1.0, 0)
        assert not combat.monster_skill_due(area, target, 110.0)   # slowed 6s -> 17.14s
        assert combat.monster_skill_due(area, target, 118.0)

        # Rotation has 8 Charges; four autos cap it at 12 before End of Time.
        for _ in range(4):
            cast(0, 90410)
        assert combat._rp[uid] == 12
        ultimate, ult_damage = cast(4, 90414)
        stasis = next(n for n in ultimate["Nodes"] if n.get("AuraName") == "Temporal Stasis")
        assert stasis["Targets"] == [target]
        assert stasis["Duration"] == 2.5 and stasis["AnimationSpeed"] == 0.0
        assert combat.is_stunned(area, target)
        assert combat.monster_attack_interval(area, target) == float("inf")
        assert not combat.monster_skill_due(area, target, 999999.0)
        assert combat._rp[uid] == 0

        hp1 = combat._mon[(area, target)]
        assert hp1 < hp0 and auto_damage > 0 and echo_damage > auto_damage
        assert slow_damage > 0 and ult_damage > slow_damage
        print("Red Dragon OK:", {"start_hp": hp0, "end_hp": hp1,
              "auto": auto_damage, "echo": echo_damage, "slow": slow_damage,
              "ultimate": ult_damage, "rewind_hp": combat.player_hp(uid)})


if __name__ == "__main__":
    run()
