"""Chronomancer seed, five-slot Forge shape, resource loop, slow, freeze, and aura metadata."""
import json
import time

import combat
import db
import forge
import seed


def run():
    db.use_throwaway()
    db.init()
    with db.connect() as conn:
        assert seed.seed_chronomancer(conn) == 5
        cls = conn.execute("SELECT * FROM classes WHERE class_id=?",
                           (seed.CHRONOMANCER_CLASS_ID,)).fetchone()
        assert cls and cls["name"] == "TimeLord"
        assert json.loads(cls["resource"])["MaxRP"] == 12
        rules = json.loads(cls["raw"])["rules"]
        links = conn.execute("SELECT slot,skill_id FROM class_skills WHERE class_id=? ORDER BY slot",
                             (seed.CHRONOMANCER_CLASS_ID,)).fetchall()
        assert [(r["slot"], r["skill_id"]) for r in links] == list(enumerate(seed._CHRONO_SKILL_IDS))
        item = db.item(conn, seed.CHRONOMANCER_ARMOR_ITEM)
        assert item["MetaString"] == str(seed.CHRONOMANCER_CLASS_ID)
        assert item["Name"] == "TimeLord" and item["Bundle"]["ID"] == 8814
        assert conn.execute("SELECT 1 FROM shop_items WHERE shop_id=? AND item_id=?",
                            (seed.CLASS_SHOP_ID, seed.CHRONOMANCER_ARMOR_ITEM)).fetchone()

        uid, area = 991, "chrono-test"
        combat.register_player(uid, 2000)
        combat.set_power(uid, {"ap": 100, "sp": 120, "tcr": 0, "scm": 1.5, "tha": 1})
        combat.set_resource_model(uid, "stacking", max_rp=12)
        for ts in ("m:1", "m:2", "m:3", "m:4"):
            combat.register_monster(area, ts, 10000)

        slow = forge.skill_by_id(conn, 90413)
        packets, _, _ = combat.cast_skill_rules(area, uid, 3, "m:1", slow["data"],
                                                 slow["forge"], 90413, rules)
        aura = next(n for n in packets[0]["Nodes"] if n.get("AuraName") == "Time Dilation")
        assert aura["Duration"] == 6.0 and aura["AnimationSpeed"] == 0.35
        assert set(aura["Targets"]) == {"m:1", "m:2", "m:3", "m:4"}
        assert combat.monster_speed_multiplier(area, "m:1") == 0.35
        assert combat.monster_attack_interval(area, "m:1") > combat.MON_ATTACK_CD * 2.8
        assert combat._rp[uid] == 3

        ultimate = forge.skill_by_id(conn, 90414)
        packets, _, _ = combat.cast_skill_rules(area, uid, 4, "m:1", ultimate["data"],
                                                 ultimate["forge"], 90414, rules)
        aura = next(n for n in packets[0]["Nodes"] if n.get("AuraName") == "Temporal Stasis")
        assert aura["Duration"] == 2.5 and aura["AnimationSpeed"] == 0.0
        assert combat.is_stunned(area, "m:1") and combat._rp[uid] == 0

        combat._auras[(area, "m:1")]["Temporal Stasis"]["ends"] = time.time() - 1
        expired = combat.aura_ticks()
        remove = [p for a, p, _ in expired if a == area and p.get("Cmd") == "AuraChange"]
        assert any(p["Target"] == "m:1" and p["nam"] == "Temporal Stasis" for p in remove)
    print("chronomancer OK: five Forge skills -> charges -> slow -> freeze -> clean expiry")


if __name__ == "__main__":
    run()
