"""
Combat Stage 3 — authored skill graphs drive damage.

Walks the exact node-graph the in-client Forge exported in testing (Big Ass Ball:
OnRequest -> Cooldown -> Damage -> Target) and checks cast_skill turns it into a
valid single-shot Attack: the Target helper resolves to the cast target, an unset
Multiplier means normal damage, a set Multiplier scales it, and an empty/unauthored
graph still produces a default hit so the slot isn't dead.
"""
import combat
import game

# the real authored graph captured from the running client
DATA = [{"0": {"Name": "OnRequest", "Slot": -1}},
        {"2": {"Name": "Cooldown", "Slot": -1, "CD": 0},
         "6": {"Name": "Damage", "DamageType": "Physical", "Multiplier": 0.0,
               "Targets": {"id": "15"}},
         "15": {"Name": "Target"}}]
FORGE = [{}, {"0": {"Next": {"id": "2", "Next": {"id": "6", "Targets": {"id": "15"}}}}}]


def _names(attack):
    return [n["Name"] for n in attack["Nodes"]]


def main():
    combat.register_monster("battleon", "m:968", 5000)

    attack, killed, dmg = combat.cast_skill("battleon", 1, 1, "m:968", DATA, FORGE)
    assert attack["Cmd"] == "Attack" and attack["Slot"] == 1
    assert attack["StatusCode"] == 1 and attack["Wait"] is True
    assert "Damage" in _names(attack) and "Cooldown" in _names(attack)
    dnode = next(n for n in attack["Nodes"] if n["Name"] == "Damage")
    assert dnode["Targets"] == ["m:968"], "Target helper must resolve to the cast target"
    assert dnode["Damages"][0] == dmg and dnode["TargetHPs"][0] == 5000 - dmg
    base = 18 <= dmg <= 110          # 18-55, x2 on crit
    assert base, f"unexpected base damage {dmg}"

    # Multiplier scales damage ~3x
    data3 = [DATA[0], dict(DATA[1])]
    data3[1]["6"] = dict(DATA[1]["6"], Multiplier=3.0)
    tot = 0
    for _ in range(400):
        combat._mon[("battleon", "m:968")] = 10 ** 7
        combat._rp.clear()              # isolate the multiplier from the Determined empower
        _, _, d = combat.cast_skill("battleon", 1, 1, "m:968", data3, FORGE)
        tot += d
    avg = tot / 400
    assert 90 < avg < 140, f"mult=3 avg {avg} not ~3x base (~110)"

    # Offensive Damage resolving to a player (Self helper) must be SUPPRESSED — never
    # send a player TargetHP (the client would read it as "set HP", which killed healers).
    data_self = [DATA[0], {"6": {"Name": "Damage", "Multiplier": 1.0, "Targets": {"id": "9"}},
                           "9": {"Name": "Self"}}]
    forge_self = [{}, {"0": {"Next": {"id": "6", "Targets": {"id": "9"}}}}]
    combat.register_player(7, 1337)
    a, _, _ = combat.cast_skill("battleon", 7, 3, "m:968", data_self, forge_self)
    assert not any(n["Name"] == "Damage" for n in a["Nodes"]), \
        "offensive Damage on a player target must be dropped (no self-kill)"
    assert combat.player_hp(7) == 1337, "casting must not damage the caster"

    # empty graph still hits (default) so the slot does something
    a, _, d = combat.cast_skill("battleon", 1, 2, "m:968", [{}, {}], [{}, {}])
    assert "Damage" in _names(a) and d > 0

    # AllEnemies helper -> server-side AoE hits every monster in the area
    for ts in ("m:101", "m:102", "m:103"):
        combat.register_monster("aoe", ts, 5000)
    data_aoe = [DATA[0], {"6": {"Name": "Damage", "Multiplier": 1.0, "Targets": {"id": "9"}},
                          "9": {"Name": "AllEnemies"}}]
    forge_aoe = [{}, {"0": {"Next": {"id": "6", "Targets": {"id": "9"}}}}]
    a, _, _ = combat.cast_skill("aoe", 5, 1, "m:101", data_aoe, forge_aoe)
    dn = next(n for n in a["Nodes"] if n["Name"] == "Damage")
    assert set(dn["Targets"]) == {"m:101", "m:102", "m:103"}, "AllEnemies must hit every area monster"
    assert len(dn["Damages"]) == 3 and all(x > 0 for x in dn["Damages"])

    # Aura / Restrict render passthrough resolve their targets
    data_buf = [DATA[0], {"6": {"Name": "Aura", "AuraName": "Rage", "Targets": {"id": "9"}},
                          "9": {"Name": "Self"}}]
    forge_buf = [{}, {"0": {"Next": {"id": "6", "Targets": {"id": "9"}}}}]
    a, _, _ = combat.cast_skill("aoe", 5, 4, None, data_buf, forge_buf)
    aura = next(n for n in a["Nodes"] if n["Name"] == "Aura")
    assert aura["AuraName"] == "Rage" and aura["Targets"] == ["p:5"]

    print(f"combat Stage 3 OK: authored graph -> {_names(attack)}, "
          f"mult=3 avg {avg:.0f}, Self->caster, AllEnemies->3 AoE, Aura passthrough, empty->default")

    # --- P0-1: Healer heals (negative-damage Damage node on ally/self p: targets) ---
    # Capture ground truth: heals are Slot-2 Damage nodes with negative Damages on p:
    # targets (e.g. [-345]->[p:self], [-342,-342,-342,-342]->4 players), raising TargetHP.
    sta_h, maxhp_h = game.build_combat_stats(
        {"stat_str": 8, "stat_end": 14, "stat_dex": 10, "stat_int": 18,
         "stat_wis": 14, "stat_lck": 10, "level": 5})
    combat.register_player(200, maxhp_h)
    combat.set_power(200, sta_h)
    combat._php[200] = 100                       # wounded healer
    HEAL = [{"0": {"Name": "OnRequest"}},
            {"6": {"Name": "Damage", "Heal": True, "Multiplier": 10.0, "Targets": {"id": "9"}},
             "9": {"Name": "Allies"}}]
    HFG = [{}, {"0": {"Next": {"id": "6", "Targets": {"id": "9"}}}}]

    # solo: heal lands on the caster only (the common captured [-345]->[p:self] case)
    a, killed, dmg = combat.cast_skill("ward", 200, 2, "m:968", HEAL, HFG)
    hn = next(n for n in a["Nodes"] if n["Name"] == "Damage")
    assert hn["Targets"] == ["p:200"], "a heal targets the caster/allies, never the clicked monster"
    assert hn["Damages"][0] < 0, "a heal is a NEGATIVE-damage popup"
    assert hn["TargetHPs"][0] == combat.player_hp(200) > 100, "TargetHP is the raised HP"
    assert dmg == 0 and not killed, "a heal deals no damage and kills nothing"

    # the cap is respected: heal hits self + up to HEAL_MAX_TARGETS-1 nearby allies
    combat._php[200] = 50
    for uid_a in (201, 202, 203, 204):
        combat.register_player(uid_a, 2000); combat._php[uid_a] = 500
    party = [f"p:{u}" for u in (200, 201, 202, 203, 204)]
    a, _, _ = combat.cast_skill("ward", 200, 2, None, HEAL, HFG, allies=party)
    hn = next(n for n in a["Nodes"] if n["Name"] == "Damage")
    assert hn["Targets"][0] == "p:200", "the caster is healed first"
    assert len(hn["Targets"]) == combat.HEAL_MAX_TARGETS, "party heal is capped (self + 3)"
    assert all(d < 0 for d in hn["Damages"]) and all(t.startswith("p:") for t in hn["Targets"])
    assert all(hp > 0 for hp in hn["TargetHPs"])
    assert combat.player_hp(201) > 500, "a nearby ally is actually healed"

    # the REAL seeded Healing Word (142) graph produces a heal, not offensive damage
    import json as _json
    import seed as _seed
    g142 = _json.loads(_seed.SKILL_GRAPHS_FILE.read_text(encoding="utf-8"))["142"]
    combat._php[200] = 100
    a, _, _ = combat.cast_skill("ward", 200, 2, "m:968", g142["data"], g142["forge"])
    hn = next(n for n in a["Nodes"] if n["Name"] == "Damage")
    assert hn["Damages"][0] < 0 and hn["Targets"] == ["p:200"], \
        "seeded Healing Word (142) must heal the caster, not damage a monster"
    print(f"P0-1 heal OK: Healing Word -> green heal {hn['Damages']} on {hn['Targets']} "
          f"(HP {combat.player_hp(200)}), party cap {combat.HEAL_MAX_TARGETS}")

    # --- Item 4: autonomous, lethal monster AI ---
    combat.register_player(42, 1337)
    combat.register_monster("lair", "m:968", 9999, mon_id=968, frame="Enter")
    combat.engage("lair", "m:968", 42)
    assert ("lair", "m:968", 42) in combat.engagements(), "attacking a monster aggros it"

    # a monster swing can MISS now (P0-4); loop until one lands to verify HP drops
    for _ in range(200):
        atk, hp, died = combat.monster_attack("lair", "m:968", 42)
        dn = atk["Nodes"][0]
        if dn["Damages"][0] > 0:
            break
    assert atk["Caster"] == "m:968" and atk["Slot"] == -1
    assert dn["Name"] == "Damage" and dn["Targets"] == ["p:42"]
    assert dn["TargetHPs"][0] == hp and hp < 1337 and not died, "HP drops (server-authoritative)"

    # a killing blow is lethal; revive heals to full and clears aggro (loop past any miss)
    combat._php[42] = 5
    for _ in range(200):
        _, hp2, died2 = combat.monster_attack("lair", "m:968", 42)
        if died2:
            break
    assert hp2 == 0 and died2, "a hit that empties HP is lethal"
    res = combat.revive_player(42, "hero")
    assert res["Cmd"] == "playerRes" and res["unm"] == "hero" and res["HP"] == combat.PLAYER_MAXHP
    assert combat.player_hp(42) == combat.PLAYER_MAXHP
    assert not any(u == 42 for _a, _m, u in combat.engagements()), "revive drops aggro"

    # dead monsters drop out of the engagement set; respawn resets HP + emits RespawnMon
    combat.engage("lair", "m:968", 42)
    combat._mon[("lair", "m:968")] = 0
    assert ("lair", "m:968", 42) not in combat.engagements(), "dead monsters stop attacking"
    rp = combat.respawn_packet("lair", "m:968")
    assert rp["Cmd"] == "RespawnMon" and rp["monMapID"] == 968 and rp["monID"] == 968
    assert combat._mon[("lair", "m:968")] == 9999, "respawn restores full HP"

    print("monster AI OK: aggro+autonomous, lethal 1337->0 + revive, "
          "respawn resets HP & emits RespawnMon")

    # --- P1-1: real spatial-hitbox handshake (igai/gai), faithful to capture ---
    # graph: OnRequest -> Range(input) -> Cooldown -> SoundFX -> AnimationHitbox(input) -> Damage
    # capture flow (own-cast): gar -> igai(Range) -> gai -> Attack(Pending)[Range,Cooldown,
    # SoundFX] -> igai(AnimationHitbox RT1) -> gai[...targets] -> Attack(Success)[AnimationHitbox,
    # Damage]. The AnimationHitbox gai returns the MONSTERS the swing actually hit (cleave).
    sdata = [{"0": {"Name": "OnRequest"}},
             {"1": {"Name": "Range", "HRange": 5, "VRange": 1},
              "2": {"Name": "Cooldown", "CD": 3000},
              "s": {"Name": "SoundFX", "Sound": "swoosh"},
              "3": {"Name": "AnimationHitbox", "X": 7, "Width": 12, "Height": 2,
                    "Animation": "Swing", "Speed": 0.75, "Time": 0.1},
              "4": {"Name": "Damage", "Multiplier": 2.0}}]
    sforge = [{}, {"0": {"Next": {"id": "1", "Next": {"id": "2", "Next": {"id": "s",
              "Next": {"id": "3", "Next": {"id": "4"}}}}}}}]
    for ts in ("m:50", "m:51", "m:52"):
        combat.register_monster("arena", ts, 100000)

    combat._rp.clear()
    pkts, killed, _ = combat.begin_cast("arena", 9, 1, "m:50", sdata, sforge)
    # gar -> first input node is Range; its igai goes out (RT 0, validate the cast target)
    assert pkts[-1]["Cmd"] == "igai" and pkts[-1]["Response"]["Name"] == "Range"
    assert pkts[-1]["ReturnType"] == 0 and pkts[-1]["Response"]["mode"] == "validate"
    ctx = pkts[-1]["ContextId"]

    # client validates the target -> Pending batch (leading non-input nodes) + the
    # AnimationHitbox igai (ReturnType 1, carrying the swing box + animation)
    pkts, killed, dmg = combat.resume_cast(ctx, ["1", ctx, "Range", "validate", "true", "m:50"])
    pend = pkts[0]
    assert pend["StatusCode"] == 2, "Pending batch before the hitbox"
    assert [n["Name"] for n in pend["Nodes"]] == ["Range", "Cooldown", "SoundFX"]
    igai_hb = pkts[-1]
    assert igai_hb["Cmd"] == "igai" and igai_hb["Response"]["Name"] == "AnimationHitbox"
    assert igai_hb["ReturnType"] == 1 and igai_hb["Response"]["inputReturn"] == 1
    assert igai_hb["Response"]["Width"] == 12 and igai_hb["Response"]["Animation"] == "Swing"
    ctx2 = igai_hb["ContextId"]

    # the client reports the swing hit THREE monsters (+ 'actor' = self, which is filtered);
    # the Damage node hits the whole set -> real cleave, not just the clicked target.
    pkts, killed, dmg = combat.resume_cast(
        ctx2, ["1", ctx2, "AnimationHitbox", "m:50", "actor", "m:51", "m:52"])
    atk = pkts[0]
    assert atk["StatusCode"] == 1, "final batch is Success"
    assert [n["Name"] for n in atk["Nodes"]] == ["AnimationHitbox", "Damage"]
    dn = next(n for n in atk["Nodes"] if n["Name"] == "Damage")
    assert set(dn["Targets"]) == {"m:50", "m:51", "m:52"}, \
        f"AnimationHitbox cleave must hit every reported monster, got {dn['Targets']}"
    assert "actor" not in dn["Targets"], "'actor' (caster self) is filtered from offensive damage"
    assert len(dn["Damages"]) == 3 and all(d != 0 or t == 0 for d, t in zip(dn["Damages"], dn["DamageTypes"]))
    assert not any(p["Cmd"] == "igai" for p in pkts), "no further input nodes -> no more igai"

    # a Hitbox node emits ReturnType 2 with no Animation (the immediate BoxCastAll variant)
    hdata = [{"0": {"Name": "OnRequest"}},
             {"1": {"Name": "Hitbox", "X": 7, "Width": 12, "Height": 2},
              "2": {"Name": "Damage", "Multiplier": 1.0}}]
    hforge = [{}, {"0": {"Next": {"id": "1", "Next": {"id": "2"}}}}]
    pkts, _, _ = combat.begin_cast("arena", 9, 1, "m:50", hdata, hforge)
    hb = pkts[-1]
    assert hb["Cmd"] == "igai" and hb["Response"]["Name"] == "Hitbox"
    assert hb["ReturnType"] == 2 and hb["Response"]["inputReturn"] == 2
    assert "Animation" not in hb["Response"], "Hitbox igai carries no Animation (capture)"

    print(f"P1-1 hitbox handshake OK: gar->igai(Range)->Pending[Range,Cooldown,SoundFX]"
          f"->igai(AnimationHitbox RT1)->gai(3 monsters)->Success Damage cleave {dn['Targets']}")

    # --- Determination: autos/skills build it; Determined (>=50) skill spends + empowers ---
    combat._rp.clear()
    uid = 77
    for _ in range(10):
        combat._apply_determination(uid, 0)            # 10 autos x5 = 50 (Determined)
    assert combat._rp[uid] >= combat.DETERMINED_AT
    total, empowered = combat._apply_determination(uid, 2)   # cast a skill while Determined
    assert empowered and total == 0, "a Determined skill spends all Determination"
    total2, emp2 = combat._apply_determination(uid, 2)       # next skill just builds again
    assert not emp2 and total2 == combat.DET_SKILL_GAIN
    print(f"determination OK: build to {combat.DETERMINED_AT} -> skill spends (empowered) -> rebuilds")

    # --- P0-2: mana model — skills SPEND, autos RESTORE, no Determined empower ---
    muid = 88
    combat.set_resource_model(muid, "mana")
    assert combat._rp[muid] == combat.MAX_RP, "a mana class starts at full mana"
    combat.set_class_mana(muid, {500: 15, 501: 20})
    total, emp = combat._apply_determination(muid, 1, 500)   # cast a 15-mana skill
    assert total == combat.MAX_RP - 15 and not emp, "a mana skill spends its cost (no empower)"
    total, _ = combat._apply_determination(muid, 2, 501)     # cast a 20-mana skill
    assert total == combat.MAX_RP - 35, "mana drains cumulatively"
    total, _ = combat._apply_determination(muid, 0)          # auto-attack restores mana
    assert total == min(combat.MAX_RP, combat.MAX_RP - 35 + combat.AUTO_MANA_REGEN), \
        "the auto-attack restores mana"
    # being at/over the determination threshold must NOT empower a mana class
    combat._rp[muid] = combat.MAX_RP
    _, emp = combat._apply_determination(muid, 2, 501)
    assert not emp, "mana classes never get the Determined empower"
    print(f"P0-2 mana OK: starts full {combat.MAX_RP}, spends per cost, auto restores "
          f"+{combat.AUTO_MANA_REGEN}, no empower")

    # --- P0-3: statUpdate carries CURRENT HP — a kill must NOT heal the player to full ---
    char_row = {"id": 9001, "stat_str": 14, "stat_end": 18, "stat_dex": 12,
                "stat_int": 11, "stat_wis": 10, "stat_lck": 13, "level": 1,
                "gold": 0, "coins": 0, "exp": 0}
    _, mhp = game.build_combat_stats(char_row)
    full = game.build_stat_update(char_row)                 # login / stat refresh -> full HP
    assert full["HP"] == full["MaxHP"] == mhp, "login statUpdate is full HP"
    wounded = game.build_stat_update(char_row, hp=137)      # after a kill at low HP
    assert wounded["MaxHP"] == mhp and wounded["HP"] == 137, "statUpdate must carry CURRENT HP"
    assert game.build_stat_update(char_row, hp=mhp + 9999)["HP"] == mhp, "HP clamps to MaxHP"
    # integration: a monster wounds the player, then the post-kill statUpdate keeps the
    # wounded HP (the value _handle_kills threads through), not MaxHP.
    combat.register_player(9001, mhp)
    for _ in range(200):                                    # loop past any monster miss (P0-4)
        combat.monster_attack("kf", "m:7", 9001)
        if combat.player_hp(9001) < mhp:
            break
    cur = combat.player_hp(9001)
    assert cur < mhp, "monster hit dropped HP"
    assert game.build_stat_update(char_row, hp=cur)["HP"] == cur, \
        "post-kill statUpdate keeps the wounded HP (no heal-on-kill)"
    print(f"P0-3 statUpdate OK: current HP carried ({cur}/{mhp}), not healed to full on kill")

    # --- P0-4: Miss/Dodge rolls -> DamageType popups 3 (Miss) and 2 (Dodge) ---
    # Feed the captured player `sta` (tha 0.9895, tcr 0.0114) and assert the emitted
    # DamageType histogram tracks the inputs: 0 (Normal) dominant, 3 (Miss) ~= (1 - tha),
    # 1 (Crit) ~= tcr, 2 (Dodge) rarest. Capture overall: {0:8285, 3:330, 1:221, 2:5}.
    import collections as _c
    cap_sta = {"ap": 31, "sp": 31, "tcr": 0.0114, "scm": 1.538, "tha": 0.9895}
    combat.set_power(300, cap_sta)
    hist = _c.Counter()
    DMG = [{"0": {"Name": "OnRequest"}},
           {"6": {"Name": "Damage", "Multiplier": 1.0, "Targets": {"id": "9"}},
            "9": {"Name": "Target"}}]
    DFG = [{}, {"0": {"Next": {"id": "6", "Targets": {"id": "9"}}}}]
    N = 40000
    for _ in range(N):
        combat._mon[("hg", "m:1")] = 10 ** 12
        a, _, _ = combat.cast_skill("hg", 300, 1, "m:1", DMG, DFG)
        dn = next(n for n in a["Nodes"] if n["Name"] == "Damage")
        hist[dn["DamageTypes"][0]] += 1
        # a miss/dodge deals 0 and leaves HP unchanged; a normal/crit deals > 0
        if dn["DamageTypes"][0] in (combat.DT_MISS, combat.DT_DODGE):
            assert dn["Damages"][0] == 0, "miss/dodge must deal 0"
        else:
            assert dn["Damages"][0] > 0, "a landed hit deals damage"
    miss_rate = hist[combat.DT_MISS] / N
    crit_rate = hist[combat.DT_CRIT] / N
    assert hist[combat.DT_NORMAL] > 0.9 * N, "Normal dominates the histogram"
    assert abs(miss_rate - (1 - cap_sta["tha"])) < 0.004, \
        f"Miss rate {miss_rate:.4f} ~= 1-tha {1-cap_sta['tha']:.4f}"
    assert abs(crit_rate - cap_sta["tcr"]) < 0.004, \
        f"Crit rate {crit_rate:.4f} ~= tcr {cap_sta['tcr']:.4f}"
    assert hist[combat.DT_DODGE] < hist[combat.DT_CRIT], "Dodge is the rarest popup (2 < 1)"
    assert hist[combat.DT_MISS] > 0 and hist[combat.DT_DODGE] >= 0, "Miss popups are emitted"

    # monsters miss too (their swing produces a 0-damage MISS popup over the player)
    mhist = _c.Counter()
    combat.register_player(301, 10 ** 9)
    for _ in range(20000):
        combat._php[301] = 10 ** 9
        atk, _, _ = combat.monster_attack("hg", "m:2", 301)
        mhist[atk["Nodes"][0]["DamageTypes"][0]] += 1
    mon_miss = mhist[combat.DT_MISS] / 20000
    assert abs(mon_miss - combat.MON_MISS_CHANCE) < 0.01, \
        f"monster miss {mon_miss:.3f} ~= MON_MISS_CHANCE {combat.MON_MISS_CHANCE}"
    print(f"P0-4 miss/dodge OK: player {{0:{hist[0]}, 3:{hist[combat.DT_MISS]}, "
          f"1:{hist[combat.DT_CRIT]}, 2:{hist[combat.DT_DODGE]}}} (miss {miss_rate:.3f}~=1-tha, "
          f"crit {crit_rate:.3f}~=tcr); monster miss {mon_miss:.3f}")

    # --- P1-2: damage formula refit to the captured `sta` + weapon term ---
    # Captured reference (statUpdate p:21675187): STR14/END18/DEX12/INT11/WIS10/LCK13 ->
    # ap31, sp31, tcr 0.0114, scm 1.538, MaxHP 1337; autos land 56-78.
    cap_char = {"id": 21675187, "stat_str": 14, "stat_end": 18, "stat_dex": 12,
                "stat_int": 11, "stat_wis": 10, "stat_lck": 13, "level": 1}
    sta_c, mhp_c = game.build_combat_stats(cap_char)
    assert abs(sta_c["tcr"] - 0.0114) < 0.002, f"tcr {sta_c['tcr']} ~= captured 0.0114 (was ~0.11)"
    assert abs(sta_c["scm"] - 1.538) < 0.01, f"scm {sta_c['scm']} ~= captured 1.538"
    assert abs(mhp_c - 1337) < 50, f"MaxHP {mhp_c} ~= captured 1337 (was ~950)"
    assert 29 <= sta_c["ap"] <= 33, f"ap {sta_c['ap']} ~= captured 31"
    su_c = game.build_stat_update(cap_char)
    assert su_c["DmgMin"] == round(sta_c["ap"] * combat.WEAPON_MIN) and \
           su_c["DmgMax"] == round(sta_c["ap"] * combat.WEAPON_MAX), "statUpdate carries the weapon range"

    # weapon term: feed the captured sta and sample many autos (mult 1) — the NON-CRIT band
    # must land ~56-78 (capture), not the old ~26-36.
    combat.set_power(310, dict(cap_sta))
    normals = []
    for _ in range(20000):
        d, dt = combat._hit("p:310", 1.0, False)
        if dt == combat.DT_NORMAL:
            normals.append(d)
    lo, hi = min(normals), max(normals)
    assert 53 <= lo and hi <= 80, f"auto band [{lo},{hi}] must sit in the captured ~56-78"
    assert 56 <= sum(normals) / len(normals) <= 78, "mean auto damage in the captured band"
    print(f"P1-2 formula OK: tcr {sta_c['tcr']} scm {sta_c['scm']} MaxHP {mhp_c} "
          f"DmgMin/Max {su_c['DmgMin']}/{su_c['DmgMax']}; auto band [{lo},{hi}] (capture 56-78)")

    # --- P1-3: monster damage scales with level (capture: avg ~7*level; lvl2~12, lvl8~56) ---
    combat.register_player(401, 10 ** 9)

    def _mon_band(level, n=15000):
        combat.register_monster("md", f"m:{level}", 100000, level=level)
        ds = []
        for _ in range(n):
            combat._php[401] = 10 ** 9
            atk, _, _ = combat.monster_attack("md", f"m:{level}", 401)
            nd = atk["Nodes"][0]
            if nd["DamageTypes"][0] == combat.DT_NORMAL:
                ds.append(nd["Damages"][0])
        return min(ds), max(ds), sum(ds) / len(ds)

    lo2, hi2, avg2 = _mon_band(2)
    lo8, hi8, avg8 = _mon_band(8)
    _, _, avg12 = _mon_band(12)
    assert 2 * 5 <= lo2 and hi2 <= 2 * 9, f"lvl2 band [{lo2},{hi2}] within [10,18]"
    assert 8 * 5 <= lo8 and hi8 <= 8 * 9, f"lvl8 band [{lo8},{hi8}] within [40,72]"
    assert avg2 < avg8 < avg12, "higher-level monsters hit harder"
    assert abs(avg8 - 7 * 8) < 6, f"lvl8 avg {avg8:.0f} ~= 7*level (capture m:968 ~56)"
    # an unleveled monster falls back to the flat range (no level known)
    combat.register_monster("md", "m:x", 100)
    combat._php[401] = 10 ** 9
    a, _, _ = combat.monster_attack("md", "m:x", 401)
    nd = a["Nodes"][0]
    assert nd["DamageTypes"][0] != combat.DT_NORMAL or \
        combat.MON_DMG[0] <= nd["Damages"][0] <= combat.MON_DMG[1], "unleveled -> flat fallback"
    # register_monster stored the level + race for later (Dragon's Bane = P2-3)
    combat.register_monster("md", "m:dragon", 5000, level=20, race="Dragon")
    assert combat._moninfo[("md", "m:dragon")]["level"] == 20
    assert combat._moninfo[("md", "m:dragon")]["race"] == "Dragon"
    print(f"P1-3 monster dmg OK: lvl2 ~{avg2:.0f} lvl8 ~{avg8:.0f} lvl12 ~{avg12:.0f} "
          f"(~7*level, capture m:968 lvl8 ~56)")

    # --- P1-4: element selects the stat (Magical -> sp/INT, Physical -> ap/STR) ---
    # A caster with high spell power but low attack power: a Magical Damage node must scale on
    # sp (so Mage damage tracks INT), a Physical node on ap (Warrior tracks STR).
    combat.set_power(500, {"ap": 10, "sp": 60, "tcr": 0.0, "scm": 1.5, "tha": 1.0})
    MAGE = [{"0": {"Name": "OnRequest"}},
            {"1": {"Name": "Damage", "DamageType": "Magical", "Multiplier": 1.0,
                   "Targets": {"id": "2"}}, "2": {"Name": "Target"}}]
    WARR = [{"0": {"Name": "OnRequest"}},
            {"1": {"Name": "Damage", "DamageType": "Physical", "Multiplier": 1.0,
                   "Targets": {"id": "2"}}, "2": {"Name": "Target"}}]
    EFG = [{}, {"0": {"Next": {"id": "1", "Targets": {"id": "2"}}}}]
    mtot = wtot = 0
    for _ in range(3000):
        combat._mon[("el", "m:1")] = 10 ** 9
        mtot += combat.cast_skill("el", 500, 1, "m:1", MAGE, EFG)[2]
        combat._mon[("el", "m:1")] = 10 ** 9
        wtot += combat.cast_skill("el", 500, 1, "m:1", WARR, EFG)[2]
    mavg, wavg = mtot / 3000, wtot / 3000
    assert mavg > wavg * 3, f"Magical(sp60) must dwarf Physical(ap10): {mavg:.0f} vs {wavg:.0f}"
    print(f"P1-4 element OK: Magical->sp avg {mavg:.0f} >> Physical->ap avg {wavg:.0f} "
          f"(element selects the stat; Mage scales on INT, not STR)")

    # --- P2-3: Dragon's Bane +20%/+50% vs Dragonkin (Dragonslayer passive/buff) ---
    import time as _time
    combat.set_power(600, {"ap": 100, "sp": 100, "tcr": 0.0, "scm": 1.5, "tha": 1.0})
    combat.set_resource_model(600, "determination")          # DS
    combat.register_monster("dn", "m:drag", 10 ** 9, race="Dragonkin")
    combat.register_monster("dn", "m:human", 10 ** 9, race="Human")
    DBG = [{"0": {"Name": "OnRequest"}},
           {"1": {"Name": "Damage", "Multiplier": 1.0, "Targets": {"id": "2"}}, "2": {"Name": "Target"}}]
    DBF = [{}, {"0": {"Next": {"id": "1", "Targets": {"id": "2"}}}}]

    def _avg(ts, n=3000):
        t = 0
        for _ in range(n):
            combat._rp[600] = 0                               # keep below Determined (no empower)
            combat._mon[("dn", ts)] = 10 ** 9
            t += combat.cast_skill("dn", 600, 1, ts, DBG, DBF)[2]
        return t / n

    combat._dragonbane.pop(600, None)                        # buff off -> passive only
    human = _avg("m:human")
    drag_passive = _avg("m:drag")
    assert 1.15 < drag_passive / human < 1.25, \
        f"passive +20% vs Dragonkin: ratio {drag_passive/human:.3f}"
    combat._dragonbane[600] = _time.time() + 10              # Dragonbane active -> +50%
    drag_bane = _avg("m:drag")
    assert 1.45 < drag_bane / human < 1.55, f"Bane +50% vs Dragonkin: ratio {drag_bane/human:.3f}"
    # casting Dragon's Bane (105) activates the buff via the real path
    combat._dragonbane.pop(600, None)
    combat._apply_determination(600, 4, 105)
    assert combat._dragonbane_active(600), "casting Dragon's Bane activates the buff"
    # a non-DS (mana) caster gets NO dragon bonus
    combat.set_power(601, {"ap": 100, "sp": 100, "tcr": 0.0, "scm": 1.5, "tha": 1.0})
    combat.set_resource_model(601, "mana")
    nd = sum(combat._hit("p:601", combat._dragon_bonus("p:601", "dn", "m:drag"), False)[0]
             for _ in range(2000)) / 2000
    base = sum(combat._hit("p:601", 1.0, False)[0] for _ in range(2000)) / 2000
    assert 0.95 < nd / base < 1.05, "non-Dragonslayer gets no Dragonkin bonus"
    print(f"P2-3 Dragon's Bane OK: passive x{drag_passive/human:.2f}, Bane x{drag_bane/human:.2f} "
          f"vs Dragonkin; non-DS x1.0; tooltips (Scorched x3/Impale 15%/Incap 3s) match")

    # --- P2-4: aura DoT/HoT ticks (type 5) + damage debuffs ---
    combat.set_power(700, {"ap": 100, "sp": 100, "tcr": 0.0, "scm": 1.5, "tha": 1.0})
    combat.register_monster("au", "m:b", 100000)
    # Bleeding = a DoT on a monster
    combat.apply_aura("au", "Bleeding", ["m:b"], "p:700")
    assert "Bleeding" in combat._auras.get(("au", "m:b"), {}), "Bleeding registered as an aura"
    combat._auras[("au", "m:b")]["Bleeding"]["next"] = 0     # force a tick due now
    hp0 = combat._mon[("au", "m:b")]
    ticks = combat.aura_ticks()
    bt = next(t for t in ticks if t[0] == "au" and t[1]["Nodes"][0]["Targets"] == ["m:b"])
    node = bt[1]["Nodes"][0]
    assert node["DamageTypes"] == [5] and node["Damages"][0] > 0, "DoT tick = positive type-5 Damage"
    assert combat._mon[("au", "m:b")] < hp0, "DoT reduced the monster's HP"

    # Radiance = a HoT on a player (negative type-5 = green)
    combat.register_player(701, 5000)
    combat.set_power(701, {"ap": 50, "sp": 50, "tcr": 0.0, "scm": 1.5, "tha": 1.0})
    combat._php[701] = 100
    combat.apply_aura("au", "Radiance", ["p:701"], "p:701")
    combat._auras[("au", "p:701")]["Radiance"]["next"] = 0
    hn = next(t[1]["Nodes"][0] for t in combat.aura_ticks()
              if t[1]["Nodes"][0]["Targets"] == ["p:701"])
    assert hn["DamageTypes"] == [5] and hn["Damages"][0] < 0 and combat.player_hp(701) > 100, \
        "HoT tick heals (negative type-5)"

    # Weakened = a damage debuff: the affected monster deals ~10% less
    combat.register_monster("au", "m:w", 100000, level=10)

    def _mavg(n=4000):
        ds = []
        for _ in range(n):
            combat._php[701] = 10 ** 9
            a, _, _ = combat.monster_attack("au", "m:w", 701)
            nd = a["Nodes"][0]
            if nd["DamageTypes"][0] == 0:
                ds.append(nd["Damages"][0])
        return sum(ds) / len(ds)

    base_avg = _mavg()
    combat.apply_aura("au", "Weakened", ["m:w"], "p:701")
    assert combat.is_dmg_debuffed("au", "m:w")
    wk_avg = _mavg()
    assert 0.85 < wk_avg / base_avg < 0.95, f"Weakened -10%: ratio {wk_avg/base_avg:.3f}"

    # expired auras are dropped
    combat._auras[("au", "m:b")]["Bleeding"]["ends"] = 0
    combat.aura_ticks()
    assert "Bleeding" not in combat._auras.get(("au", "m:b"), {}), "expired aura dropped"
    print(f"P2-4 aura OK: Bleeding DoT(type 5) ticks, Radiance HoT heals, "
          f"Weakened x{wk_avg/base_avg:.2f} dmg, auras expire")

    # --- P3-3/P3-4: dead DTYPE map removed; auto-attack uses stat damage (not flat 18-55) ---
    assert not hasattr(combat, "DTYPE"), "dead DTYPE map removed (P3-3)"
    combat.set_power(800, {"ap": 100, "sp": 100, "tcr": 0.0, "scm": 1.5, "tha": 1.0})
    combat.register_monster("aa", "m:1", 10 ** 9)
    ds = []
    for _ in range(2000):
        combat._mon[("aa", "m:1")] = 10 ** 9
        combat._last.pop(800, None)              # bypass the auto cooldown for sampling
        atk, _, dmg = combat.auto_attack("aa", "m:1", 800)
        dn = next((n for n in atk["Nodes"] if n["Name"] == "Damage"), None)
        if dn and dn["DamageTypes"][0] == 0:
            ds.append(dmg)
    aa_avg = sum(ds) / len(ds)
    assert 180 <= aa_avg <= 250, f"auto-attack now stat-based (avg {aa_avg:.0f}), not flat 18-55"
    print(f"P3-3/4 OK: dead DTYPE removed; auto-attack stat-based (avg {aa_avg:.0f} @ ap100, was ~36)")

    # --- per-skill Determined effects (Scorched triple / Impale heal / Incap stun) ---
    sta2, maxhp2 = game.build_combat_stats(
        {"stat_str": 30, "stat_end": 20, "stat_dex": 15, "stat_int": 10,
         "stat_wis": 10, "stat_lck": 15, "level": 5})
    combat.register_player(7, maxhp2)
    combat.set_power(7, sta2)
    DG = [{"0": {"Name": "OnRequest"}},
          {"1": {"Name": "Damage", "Multiplier": 1.0, "Targets": {"id": "2"}}, "2": {"Name": "Target"}}]
    FG = [{}, {"0": {"Next": {"id": "1", "Targets": {"id": "2"}}}}]

    def _cast(skill_id, determined):
        combat._mon[("z", "m:9")] = 10 ** 9
        combat._rp[7] = combat.DETERMINED_AT if determined else 0
        return combat.cast_skill("z", 7, 1, "m:9", DG, FG, skill_id)

    base = sum(_cast(167, False)[2] for _ in range(80)) / 80
    emp = sum(_cast(167, True)[2] for _ in range(80)) / 80
    assert emp > base * 2.2, f"Scorched Determined must ~3x (base {base:.0f} emp {emp:.0f})"

    combat._php[7] = 100
    heal_attack = _cast(103, True)[0]
    # a heal is a NEGATIVE-damage Immediate node on the caster (green popup); type 0 = Normal
    assert any(n["Name"] == "Damage" and n["Damages"] and n["Damages"][0] < 0
               and n.get("Immediate") for n in heal_attack["Nodes"]) \
        and combat._php[7] > 100, "Impale Determined heals the caster (negative-damage popup)"

    stun_attack = _cast(104, True)[0]
    assert any(n["Name"] == "Aura" and n.get("AuraName") == "Stunned"
               for n in stun_attack["Nodes"]) \
        and combat.is_stunned("z", "m:9"), "Incapacitate Determined = Stunned aura + server stun"

    # Scorched empowered = 3 SEPARATE Damage nodes (triple-strike), not one big hit
    scorched = _cast(167, True)[0]
    assert sum(1 for n in scorched["Nodes"] if n["Name"] == "Damage") == 3, \
        "Scorched Determined must emit 3 Damage hits"

    # continuous auto-attack engagement round-trips
    combat.auto_engage(7, "z", "m:9", DG, FG, 2000)
    assert any(u == 7 for u, *_ in combat.auto_engagements()), "auto engaged"
    combat.auto_disengage(7)
    assert not any(u == 7 for u, *_ in combat.auto_engagements()), "auto disengaged"
    print(f"empowered FX OK: Scorched x{emp/max(base,1):.1f}, Impale heal, Incap stun; auto-engage round-trips")

    # monster telegraphed tile skills (Ragnafluff): rotation pacing -> reported hit damage
    combat.register_monster("rf", "m:5", hp=5000, mon_id=364, level=20)
    tuid = 7777
    combat._php[tuid] = combat.PLAYER_MAXHP
    # an unarmed monster (no cast yet) -> a stale gmah report deals nothing
    a0, hp0, _ = combat.monster_tile_hit("rf", "m:5", tuid, 1)
    assert a0 is None and hp0 == combat.PLAYER_MAXHP, "no armed skill -> no damage"
    # first cast always due; rotation index starts at -1 so the loop advances to 0
    assert combat.monster_skill_index("rf", "m:5") == -1, "no cast yet"
    assert combat.monster_skill_due("rf", "m:5", 1000.0), "first cast is always due"
    # arm skill idx 0 with a 4.5s cd and mult 1.5; a reported hit deals ~ level swing * 1.5
    combat.arm_monster_skill("rf", "m:5", 1000.0, 4.5, 1.5, 0)
    assert combat.monster_skill_index("rf", "m:5") == 0
    assert not combat.monster_skill_due("rf", "m:5", 1004.0), "on its 4.5s cooldown"
    assert combat.monster_skill_due("rf", "m:5", 1005.0), "off cooldown after 4.5s"
    atk, hp1, died = combat.monster_tile_hit("rf", "m:5", tuid, 1)
    dmg = atk["Nodes"][0]["Damages"][0]
    assert atk["Nodes"][0]["Targets"] == ["p:7777"] and atk["Nodes"][0]["Immediate"] is True
    assert 100 <= dmg <= 300 and hp1 == combat.PLAYER_MAXHP - dmg, ("lvl20 swing*1.5", dmg)
    assert not died
    # next cast advances the rotation and can carry its own cooldown
    combat.arm_monster_skill("rf", "m:5", 1005.0, 7.0, 1.5, 1)
    assert combat.monster_skill_index("rf", "m:5") == 1, "rotation advanced"
    assert not combat.monster_skill_due("rf", "m:5", 1011.0), "honors the 7s cd of skill 1"
    # disengage clears the armed skill (no lingering damage / rotation after the fight)
    combat.disengage("rf", "m:5")
    assert combat.monster_skill_index("rf", "m:5") == -1, "rotation reset on disengage"
    assert combat.monster_tile_hit("rf", "m:5", tuid, 1)[0] is None, "armed skill cleared"
    print("monster tile skill OK: rotation index + per-skill cooldown, reported-hit dmg "
          "(lvl20 x1.5), cleared on disengage")

    # summoned adds (Ragnafluff's clones): register -> aggro -> die-for-good, capped by max_alive
    combat.register_monster("rf2", "m:9", hp=200000, mon_id=364, level=20)
    boss = "m:9"
    assert combat.live_summon_count("rf2", boss) == 0
    c1 = combat.add_summon("rf2", boss, mon_id=380, hp=4000, level=5, frame="R2")
    c2 = combat.add_summon("rf2", boss, mon_id=380, hp=4000, level=5, frame="R2")
    assert combat.is_summoned_ts(c1) and c1 != c2, ("negative unique ids", c1, c2)
    assert combat.live_summon_count("rf2", boss) == 2, "two adds alive"
    # an add is a real fightable monster: it has HP and can swing at its target
    combat.engage("rf2", c1, 8888)
    assert any(t == c1 for _, t, _ in combat.engagements()), "add aggro'd"
    combat._php[8888] = combat.PLAYER_MAXHP
    # a monster swing can MISS/DODGE now (P0-4); loop until one lands to verify the add hits
    for _ in range(200):
        combat._php[8888] = combat.PLAYER_MAXHP
        atk, _, _ = combat.monster_attack("rf2", c1, 8888)
        if atk["Nodes"][0]["Damages"][0] > 0:
            break
    assert atk["Caster"] == c1 and atk["Nodes"][0]["Damages"][0] > 0, "add melees (lvl5)"
    # killing an add forgets it (no respawn) and frees a summon slot
    combat._mon[("rf2", c1)] = 0
    combat.forget_summon("rf2", c1)
    assert combat.live_summon_count("rf2", boss) == 1, "dead add freed its slot"
    print("monster summon OK: unique negative ids, adds aggro + melee, die-for-good frees slots")

    # --- Conviction (Paladin/Reduxidain class 69420): stacks build/consume + per-stack scaling ---
    import time as _t
    puid = 4242
    combat.register_player(puid, 2000)
    combat.set_power(puid, {"ap": 30, "sp": 30, "tcr": 0.0, "scm": 1.5, "tha": 1.0})
    combat.set_resource_model(puid, "conviction", 50)
    assert combat._rp[puid] == 0, "conviction starts empty"
    total, emp = combat._apply_determination(puid, 0, 90373)      # auto: +3 stacks
    assert total == combat.CONV_AUTO_GAIN and not emp
    total, emp = combat._apply_determination(puid, 1, 90369)      # Vow: +2 stacks
    assert total == combat.CONV_AUTO_GAIN + 2 and not emp
    combat._rp[puid] = 49
    total, _ = combat._apply_determination(puid, 1, 90369)
    assert total == 50, "stacks cap at the class MaxRP (50), not the global 100"
    # per-stack scaling: Vow at 50 stacks = 1 + 0.03*50 = 2.5x (and the recorded stacks are
    # consumed by the mult so the next cast re-records)
    combat._rp[puid] = 50
    combat._apply_determination(puid, 1, 90369)
    assert abs(combat._conviction_mult(puid, 90369) - 2.5) < 1e-9
    # Smite consumes ALL stacks (no DS empower) and scales on the CONSUMED count: 1+0.05*50=3.5
    combat._rp[puid] = 50
    total, emp = combat._apply_determination(puid, 4, 90372)
    assert total == 0 and not emp, "Smite empties the pool without the Determined empower"
    assert abs(combat._conviction_mult(puid, 90372) - 3.5) < 1e-9
    combat._rp[puid] = 20
    combat._apply_determination(puid, 3, 90371)                   # Guard: no gain, no scaling
    assert combat._rp[puid] == 20 and combat._conviction_mult(puid, 90371) == 1.0
    print(f"conviction OK: auto +{combat.CONV_AUTO_GAIN} / Vow +2 / cap 50, Vow 2.5x @50, "
          f"Smite consumes-all 3.5x")

    # Smite end-to-end: the cast's lifelink heals the caster for 30% of the damage dealt,
    # and its Resource node reports the emptied pool
    combat.register_monster("pal", "m:70", 10 ** 7)
    sm_data = [{"0": {"Name": "OnRequest"}},
               {"1": {"Name": "Cooldown", "CD": 6000},
                "2": {"Name": "Damage", "DamageType": "Magical", "Multiplier": 1.5},
                "3": {"Name": "Resource", "Amount": 0}}]
    sm_forge = [{}, {"0": {"Next": {"id": "1", "Next": {"id": "2", "Next": {"id": "3"}}}}}]
    combat._rp[puid] = 50
    combat._php[puid] = 1000
    atk, _, dmg = combat.cast_skill("pal", puid, 4, "m:70", sm_data, sm_forge, 90372)
    heal = next(n for n in atk["Nodes"] if n["Name"] == "Damage" and n["Damages"][0] < 0)
    assert heal["Damages"][0] == -max(1, round(dmg * 0.30)), "lifelink = 30% of damage dealt"
    assert heal["Targets"] == [f"p:{puid}"] and heal["Immediate"] is True
    assert combat.player_hp(puid) == 1000 + max(1, round(dmg * 0.30))
    rn = next(n for n in atk["Nodes"] if n["Name"] == "Resource")
    assert rn["Amount"] == 0, "Smite's Resource node reports the emptied pool"
    assert atk["Nodes"][-1]["Name"] == "Resource", \
        "the Resource (bar-set) must be the LAST node so the consume shows live, not next cast"
    print(f"smite OK: {dmg} dmg -> lifelink heal {-heal['Damages'][0]}, pool reported empty")

    # Guaranteed: a Damage node authored Guaranteed skips the to-hit/dodge roll — Smite must
    # ALWAYS land (it spends the whole pool, so a whiff would feel awful). Cripple to-hit to a
    # near-zero (truthy — set_power coerces a falsy 0.0 back to 1.0) so a normal hit misses
    # almost always, and prove Guaranteed never does.
    combat.set_power(puid, {"ap": 30, "sp": 30, "tcr": 0.0, "scm": 1.5, "tha": 0.001})
    gsm_data = [{"0": {"Name": "OnRequest"}},
                {"1": {"Name": "Damage", "DamageType": "Magical", "Multiplier": 1.5,
                       "Guaranteed": True}}]
    gsm_forge = [{}, {"0": {"Next": {"id": "1"}}}]
    for _ in range(80):
        combat._mon[("pal", "m:70")] = 10 ** 7
        combat._rp[puid] = 10
        a, _, d = combat.cast_skill("pal", puid, 4, "m:70", gsm_data, gsm_forge, 90372)
        dn = next(n for n in a["Nodes"] if n["Name"] == "Damage")
        assert combat.DT_MISS not in dn["DamageTypes"] and combat.DT_DODGE not in dn["DamageTypes"]
        assert d > 0, "a Guaranteed Smite always deals damage (never a 0 miss)"
    # control: the SAME node without the flag misses at tha 0.001 — so the flag is what matters
    nsm_data = [{"0": {"Name": "OnRequest"}},
                {"1": {"Name": "Damage", "DamageType": "Magical", "Multiplier": 1.5}}]
    misses = 0
    for _ in range(80):
        combat._mon[("pal", "m:70")] = 10 ** 7
        combat._rp[puid] = 10
        _, _, dc = combat.cast_skill("pal", puid, 1, "m:70", nsm_data, gsm_forge, 90369)
        misses += (dc == 0)
    assert misses > 70, f"without Guaranteed, tha 0.001 misses ~always (got {misses}/80)"
    print(f"guaranteed-hit OK: Smite never misses over 80 casts; unflagged control missed {misses}/80")

    # Vow end-to-end: the damage the client sees must actually rise with stacks (the reported
    # symptom was "didn't notice damage getting higher"). Single-shot now, tha 1.0 so no misses.
    combat.set_power(puid, {"ap": 40, "sp": 40, "tcr": 0.0, "scm": 1.5, "tha": 1.0})
    vow_data = [{"0": {"Name": "OnRequest"}},
                {"1": {"Name": "Damage", "DamageType": "Physical", "Multiplier": 1.2},
                 "2": {"Name": "Resource", "Amount": 2}}]
    vow_forge = [{}, {"0": {"Next": {"id": "1", "Next": {"id": "2"}}}}]

    def _vow_avg(stacks):
        tot = 0
        for _ in range(300):
            combat._mon[("pal", "m:70")] = 10 ** 9
            combat._rp[puid] = stacks
            _, _, d = combat.cast_skill("pal", puid, 1, "m:70", vow_data, vow_forge, 90369)
            tot += d
        return tot / 300
    lo, hi = _vow_avg(0), _vow_avg(50)
    # 50 stacks = +150% -> 2.5x the 0-stack damage (±roll noise)
    assert 2.3 < hi / lo < 2.7, f"Vow @50 stacks must ~2.5x its base ({lo:.0f}->{hi:.0f})"
    print(f"vow scaling OK: {lo:.0f} dmg @0 stacks -> {hi:.0f} @50 ({hi/lo:.2f}x)")

    # InfinityHero Meteor (slot 3): the support/offense buttons select its Healer/Warrior
    # branch. Healer hits at most four room enemies and applies Suppression to each; Warrior
    # hits only the clicked target at +50% and attaches the five-second burning-field DoT.
    combat.set_power(puid, {"ap": 30, "sp": 100, "tcr": 0.0, "scm": 1.5, "tha": 1.0})
    for mid in range(72, 77):
        combat.register_monster("pal", f"m:{mid}", 10 ** 7)
    combat._mon[("pal", "m:70")] = 10 ** 7
    meteor_data = [{"0": {"Name": "OnRequest"}},
                   {"1": {"Name": "Cooldown", "CD": 4000},
                    "2": {"Name": "AllEnemies"},
                    "3": {"Name": "Damage", "DamageType": "Magical", "Multiplier": 1.0,
                          "MaxTargets": 4, "Targets": {"id": "2"}}}]
    meteor_forge = [{}, {"0": {"Next": {"id": "1", "Next": {"id": "2",
                    "Next": {"id": "3"}}}}}]

    selector_data = [{"0": {"Name": "OnRequest"}}, {"1": {"Name": "Cooldown", "CD": 4000}}]
    selector_forge = [{}, {"0": {"Next": {"id": "1"}}}]
    combat.cast_skill("pal", puid, 3, None, selector_data, selector_forge, 90371)
    assert combat.active_aspect(puid) == "healer", "slot 4 selects Healer Aspect"
    combat._rp[puid] = 20
    atk, _, _ = combat.cast_skill("pal", puid, 2, "m:70",
                                  meteor_data, meteor_forge, combat.INFINITY_METEOR_SKILL_ID)
    hn = next(n for n in atk["Nodes"] if n["Name"] == "Damage")
    ha = next(n for n in atk["Nodes"] if n.get("AuraName") == "Suppression")
    assert len(hn["Targets"]) == 4 and ha["Targets"] == hn["Targets"], \
        "Healer Meteor hits and suppresses exactly four targets"
    assert all(combat.is_dmg_debuffed("pal", ts) for ts in hn["Targets"])
    assert next(n for n in atk["Nodes"] if n["Name"] == "Cooldown")["CD"] == 4000
    assert combat._rp[puid] == 20, "Meteor neither gains nor consumes Conviction"

    combat.cast_skill("pal", puid, 4, "m:76", selector_data, selector_forge, 90372)
    assert combat.active_aspect(puid) == "warrior", "slot 5 selects Warrior Aspect"
    wp = combat._meteor_damage_props(puid, meteor_data[1]["3"])
    assert wp["Multiplier"] == 1.5 and wp["MaxTargets"] == 1 and "Targets" not in wp
    combat._rp[puid] = 20
    atk, _, _ = combat.cast_skill("pal", puid, 2, "m:76",
                                  meteor_data, meteor_forge, combat.INFINITY_METEOR_SKILL_ID)
    wn = next(n for n in atk["Nodes"] if n["Name"] == "Damage")
    wa = next(n for n in atk["Nodes"] if n.get("AuraName") == "Burning Field")
    assert wn["Targets"] == ["m:76"] and wa["Targets"] == ["m:76"]
    burning = combat._auras[("pal", "m:76")]["Burning Field"]
    assert 4.8 <= burning["ends"] - _t.time() <= 5.0, "burning field lasts five seconds"
    burning["next"] = 0
    ticks = combat.aura_ticks()
    assert any(n.get("DamageTypes") == [combat.DT_DOT] and n.get("Targets") == ["m:76"]
               for _, packet, _ in ticks for n in packet.get("Nodes", [])), \
        "burning field deals an INT/WIS-scaled magical tick"
    # The leaked giant-sword composite is a victim-side Particle, not a caster attachment.
    visual_data = [{"0": {"Name": "OnRequest"}},
                   {"1": {"Name": "PlayerAnimation", "Animation": "Attack1"},
                    "2": {"Name": "Target"},
                    "3": {"Name": "Particle", "Particle": "classInfinityHero_S1_P4",
                          "Animation": "Attack1", "Time": 0, "X": 0, "Y": 0,
                          "AnimSpeed": 0.8, "Lifetime": 6000, "Targets": {"id": "2"}}}]
    visual_forge = [{}, {"0": {"Next": {"id": "1", "Next": {"id": "2",
                    "Next": {"id": "3"}}}}}]
    vatk, _, _ = combat.cast_skill("pal", puid, 2, "m:76",
                                    visual_data, visual_forge, 999999)
    vfx = next(n for n in vatk["Nodes"] if n["Name"] == "Particle")
    assert vfx["Particle"] == "classInfinityHero_S1_P4"
    assert vfx["Targets"] == ["m:76"], "giant sword must spawn over the victim"
    assert vfx["Lifetime"] == 6000 and vfx["AnimSpeed"] == 0.8
    print("meteor OK: 4s CD, Healer 4-target Suppression, Warrior +50% single-target + 5s DoT; "
          "S1_P4 sword/lightning composite targets victim for full 6s sequence")
    # Guard: lands on the caster (not the monster), -25% incoming, outgoing bonus from stacks
    g_data = [{"0": {"Name": "OnRequest"}},
              {"1": {"Name": "Aura", "AuraName": "Paladin's Guard", "Duration": 6,
                     "MaxTargets": 6}}]
    g_forge = [{}, {"0": {"Next": {"id": "1"}}}]
    combat._rp[puid] = 50
    atk, _, _ = combat.cast_skill("pal", puid, 3, "m:70", g_data, g_forge, 90371)
    an = next(n for n in atk["Nodes"] if n["Name"] == "Aura")
    assert an["Targets"] == [f"p:{puid}"], "Guard is a party buff — never lands on the monster"
    assert combat._guard_reduction("pal", f"p:{puid}") == 0.25
    assert abs(combat._guard_dmg_bonus(f"p:{puid}") - (0.10 + 0.002 * 50)) < 1e-9, \
        "outgoing bonus snapshots the caster's stacks at cast (+0.2%/stack)"
    # a lvl-20 monster swings 100-180; guarded that lands 75-135 — every hit must respect the cap
    combat.register_monster("pal", "m:71", 5000, level=20)
    for _ in range(50):
        combat._php[puid] = 2000
        a2, _, _ = combat.monster_attack("pal", "m:71", puid)
        d2 = a2["Nodes"][0]["Damages"][0]
        assert d2 == 0 or 75 <= d2 <= 135, f"guarded swing outside the -25% band: {d2}"
    # expiry drops the effect AND emits the AuraChange remove (the Dragonbane lesson)
    combat._auras[("pal", f"p:{puid}")]["Paladin's Guard"]["ends"] = _t.time() - 1
    removes = [pk for _, pk, _ in combat.aura_ticks() if pk.get("Cmd") == "AuraChange"]
    assert any(pk["nam"] == "Paladin's Guard" and pk["Target"] == f"p:{puid}" for pk in removes)
    assert combat._guard_reduction("pal", f"p:{puid}") == 0.0
    print("guard OK: ally-targeted, -25% incoming enforced over 50 swings, +20% outgoing @50, "
          "expiry sends the aura remove")

    # decay: idle past the grace -> 5 stacks per step, step-throttled, reported for the hpmp push
    combat._rp[puid] = 30
    combat._conv_last_cast[puid] = _t.time() - (combat.CONV_DECAY_IDLE + 1)
    combat._conv_next_decay.pop(puid, None)
    decayed = dict(combat.conviction_decay())
    assert decayed.get(puid) == 30 - combat.CONV_DECAY_RATE, "idle conviction drains per step"
    assert puid not in dict(combat.conviction_decay()), "decay is throttled between steps"
    pk = combat.resource_packet(puid, "Redux")
    assert pk["Cmd"] == "hpmp" and pk["unm"] == "redux" and pk["RP"] == combat._rp[puid]
    # mana/determination regression: the conviction machinery never touches other models
    assert combat._resource_model.get(muid) == "mana"
    assert all(u == puid for u, _ in [(puid, 0)] + list(decayed.items())), "only conviction decays"
    assert combat._conviction_mult(muid, 90369) == 1.0, "non-conviction casters never scale"
    print("conviction decay OK: 10s grace -> -5/step throttled, hpmp re-sync, no model bleed")
    print("ALL COMBAT TESTS PASSED")


if __name__ == "__main__":
    main()
