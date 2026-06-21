"""
Skill Forge Stage 1 — the sfInit payload that opens the editor.

Verifies the s2c sfInit we build from the DB is shaped the way the decompiled
client expects (SkillForge.cs + ResponseSfInit.Execute): five palette categories,
a class map keyed by name with string IDs + slot->skillID, a skills library keyed
by id, every skill carrying a valid two-element Data/ForgeData graph, and the whole
thing JSON-serializable (it goes out on the wire as JSON).
"""
import json
import pathlib
import tempfile
import db
import seed
import forge


def main():
    # Run against a throwaway DB so round-trip edits never touch the live data.
    db.use_throwaway()
    seed.run()
    with db.connect() as conn:
        init = forge.build_init(conn)

    assert init["Cmd"] == "sfInit"
    for cat in ("headers", "nodes", "helpers", "conditionals", "activators"):
        assert cat in init and isinstance(init[cat], dict), f"missing palette cat {cat}"

    # classes: keyed by name, ID is a string, Skills maps slot->skillID
    assert init["classes"], "no classes seeded"
    for name, cl in init["classes"].items():
        assert isinstance(cl["ID"], str), f"{name} ID must be string"
        assert isinstance(cl["Skills"], dict)
    # Dragonslayer must keep the initPlayer template's ClassID so the active
    # character's class auto-selects in the editor.
    assert init["classes"].get("Dragonslayer", {}).get("ID") == "1932", \
        "Dragonslayer must be ID 1932 (matches initPlayer template)"
    # P2-2: real captured base-class ClassIDs (Healer 17, Warrior 33 mined from initPlayer
    # playerInfo.ClassID + user.sClass). Mage stays placeholder 2 (uncaptured).
    assert init["classes"].get("Healer", {}).get("ID") == "17", "Healer real ClassID is 17"
    assert init["classes"].get("Warrior", {}).get("ID") == "33", "Warrior real ClassID is 33"

    # skills: keyed by id; each a valid Skill object with a 2-element graph
    assert init["skills"], "no skills seeded"
    for sid, sk in init["skills"].items():
        assert sk["ID"] == int(sid)
        for f in ("Action", "Name", "Description", "Icon", "Slot",
                  "AutoHRange", "AutoVRange", "mana"):
            assert f in sk, f"skill {sid} missing {f}"
        for graph in ("Data", "ForgeData"):
            assert isinstance(sk[graph], list) and len(sk[graph]) == 2, \
                f"skill {sid} {graph} must be a 2-element JArray"

    # the palette every class references must be loadable, and the whole
    # payload must serialize (the transport is UTF-8 JSON terminated by 0x00).
    blob = json.dumps(init)
    assert len(blob) > 0

    print(f"sfInit OK: classes={list(init['classes'])} "
          f"skills={len(init['skills'])} "
          f"palette={ {k: len(init[k]) for k in ('headers','nodes','helpers','conditionals','activators')} } "
          f"({len(blob)} bytes on wire)")

    # --- Stage 2: mutation round-trips persist + reopen shows the change ---
    GRAPH = json.dumps([{"h1": {"Name": "OnRequest"}}, {"n1": {"Name": "Damage"}}])
    with db.connect() as conn:
        mage_id = int(init["classes"]["Mage"]["ID"])     # 2

        # sfSave: edit Fireball (135..139 are Mage's), rename + author a graph,
        # leave it in the same slot.
        fireball = init["classes"]["Mage"]["Skills"]["2"]   # 136
        r = forge.sf_save(conn, [mage_id, 2, 2, fireball, 0, "Inferno",
                                 "hot", "Mage/fireball", GRAPH, "[]"])
        assert r["Cmd"] == "sfUpdate" and r["Skill"] == fireball
        assert r["Data"]["Name"] == "Inferno"
        assert len(r["Data"]["Data"]) == 2 and r["Data"]["Data"][1]["n1"]["Name"] == "Damage"

        # sfNew: brand-new skill on the Mage in a fresh slot
        r = forge.sf_new(conn, [mage_id, 0, "Frost Nova", "brr", "Mage/ice", 5, "[]", "[]"])
        assert r["Cmd"] == "sfNew"
        new_sid = r["Skill"]
        assert r["Data"]["Data"] == [{}, {}]               # empty "[]" normalised

        # sfClone: copy Magic Missile into a new id on the Mage
        r = forge.sf_clone(conn, [mage_id, 135])
        assert r["Cmd"] == "sfClone" and r["Copy"] == 135
        clone_sid = r["Skill"]

        # sfLink: share an existing skill (Warrior's 114) onto the Mage
        r = forge.sf_link(conn, [mage_id, 114])
        assert r["Cmd"] == "sfLink" and r["Skill"] == 114

        # sfDel: remove whatever sits in Mage slot 4 now
        r = forge.sf_del(conn, [mage_id, 4])
        assert r["Cmd"] == "sfRemove"

        # error path: editing a non-existent skill returns sfError, not a crash
        r = forge.sf_edit(conn, [99999, 0, "x", "", "", "[]", "[]"])
        assert r["Cmd"] == "sfError"

    # reopen: a fresh sfInit must reflect every persisted change
    with db.connect() as conn:
        init2 = forge.build_init(conn)
    assert init2["skills"][str(fireball)]["Name"] == "Inferno", "edit didn't persist"
    assert str(new_sid) in init2["skills"], "new skill didn't persist"
    assert str(clone_sid) in init2["skills"], "clone didn't persist"
    mage2 = init2["classes"]["Mage"]["Skills"]
    assert "5" in mage2 and mage2["5"] == new_sid, "new skill not on class slot"
    assert "4" not in mage2, "deleted slot still present"
    assert 114 in mage2.values(), "linked skill not on class"
    print(f"mutations OK: Fireball->Inferno, +new#{new_sid}, +clone#{clone_sid}, "
          f"linked 114, deleted slot 4; reopen reflects all")

    # --- sEAct served from DB (HUD skill bar) + class-armor switching ---
    with db.connect() as conn:
        se = forge.build_seact(conn, 1932)
        assert se["Cmd"] == "sEAct"
        assert set(se["skillList"].keys()) >= {"0", "1", "2", "3", "4"}
        s0 = se["skillList"]["0"]
        for f in ("id", "act", "nam", "icon", "desc", "autoHRange", "autoVRange"):
            assert f in s0, f"sEAct slot0 missing {f}"
        # class armor item id (from the bundle filename) maps back to its class
        # the Mage class-armor item (eqp.Class.ID from the rig) switches to the Mage class
        mage_rig = conn.execute("SELECT rig FROM classes WHERE name='Mage'").fetchone()
        mage_item = json.loads(mage_rig["rig"])["ID"]
        assert forge.class_for_armor_item(conn, mage_item) == int(init["classes"]["Mage"]["ID"]), \
            "the Mage class armor must switch to the Mage class"
    print(f"sEAct OK: Dragonslayer skill bar slots {sorted(se['skillList'])}, "
          f"armor 15774->Mage switch wired")

    # --- P0-2: per-class resource model (updateClass) + mana costs ---
    # Capture ground truth: DS bar = white(16777215)/MaxRP100/Threshold50/orange(16745728);
    # every other class = blue(255)/MaxRP100/no-threshold(-1/-1).
    with db.connect() as conn:
        ds = forge.build_updateclass(conn, 1932, 42)
        assert ds["Cmd"] == "updateClass" and ds["uid"] == 42
        assert ds["ResourceColor"] == 16777215 and ds["MaxRP"] == 100
        assert ds["Threshold"] == 50 and ds["ThresholdColor"] == 16745728, \
            "Dragonslayer = Determination bar (white, orange at 50)"
        assert ds["RP"] == 0, "Determination starts empty"
        assert forge.resource_for_class(conn, 1932)["model"] == "determination"

        mage_cid = int(init["classes"]["Mage"]["ID"])
        mg = forge.build_updateclass(conn, mage_cid, 42)
        assert mg["ResourceColor"] == 255 and mg["Threshold"] == -1 and mg["ThresholdColor"] == -1, \
            "mana classes = blue bar, no threshold"
        assert mg["MaxRP"] == 100 and mg["RP"] == 100, "mana classes start full"
        assert forge.resource_for_class(conn, mage_cid)["model"] == "mana"

        # an unknown class falls back to the mana/blue default (never crashes)
        fb = forge.build_updateclass(conn, 999999, 42)
        assert fb["ResourceColor"] == 255 and fb["Threshold"] == -1

        # mana costs: cost = max(0, -regMana). The capture carries regMana ONLY on act=0
        # Regular skills (Holy -20, Heartbeat -10, ...); act=2 Flex skills (Healing Word,
        # Arcane Shield) carry NO regMana, so they don't spend via RegularMana (faithful to
        # capture — their mana spend, if any, is server-internal via a Resource node).
        healer_cid = int(init["classes"]["Healer"]["ID"])
        costs = forge.class_mana_costs(conn, healer_cid)
        assert costs.get(144) == 20, f"Holy (act=0) should cost 20 mana, got {costs.get(144)}"
        assert costs.get(141) == 10, f"Heartbeat (act=0) should cost 10 mana, got {costs.get(141)}"
        assert costs.get(142) == 0, "Healing Word (act=2 Flex) carries no regMana in capture"
        assert any(v > 0 for v in costs.values()), "mana class must have spend-able skills"
    print("P0-2 resource OK: DS=Determination(white/50/orange), mana=blue/no-threshold, "
          "act0 skills spend regMana (Holy 20, Heartbeat 10)")

    # --- P1-4: authored element + multiplier landed on the mined graphs ---
    with db.connect() as conn:
        def _dmg_node(sid):
            data = json.loads(conn.execute("SELECT data FROM skills WHERE skill_id=?",
                                           (sid,)).fetchone()["data"])
            return next((v for v in data[1].values() if v.get("Name") == "Damage"), None)
        holy = _dmg_node(144)           # Healer Holy -> Magical x2 (not mutated by this test)
        assert holy["DamageType"] == "Magical" and holy["Multiplier"] == 2.0, \
            f"Holy must be authored Magical x2, got {holy}"
        explo = _dmg_node(138)          # Mage Explosion (AoE) -> Magical x1.5
        assert explo["DamageType"] == "Magical" and explo["Multiplier"] == 1.5
        imbal = _dmg_node(116)          # Warrior Imbalancing Strike -> Physical x1.5
        assert imbal["DamageType"] == "Physical" and imbal["Multiplier"] == 1.5
        heal = _dmg_node(142)           # Healing Word stays a HEAL (not overridden by P1-4)
        assert heal.get("Heal") is True, "Healing Word must remain a heal, not get an element"
    print("P1-4 element OK: Holy/Explosion=Magical, Imbalancing=Physical, Healing Word stays heal")
    print("ALL FORGE TESTS PASSED")


if __name__ == "__main__":
    main()
