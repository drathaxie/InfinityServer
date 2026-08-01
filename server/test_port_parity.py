"""
Stage-4 port parity — Paladin Conviction + Voidwalker Hunger as DATA must be
behaviour-identical to the Python special-cases in combat.py.

Protocol: the same cast sequence runs twice from an identical reset — once
through combat.cast_skill (the live Python path), once through
combat_engine.live.cast_skill_data driving the class's PALADIN_RULES /
VOID_RULES config. random.seed is pinned per cast, and both paths roll damage
through the same combat._hit, so every number must match, not just shapes.

The ONLY tolerated wire difference (normalized below, documented in
docs/combat-engine/port_parity_report.md): the Python path always spells
PlayerAnimation Speed:1.0 while the engine omits it when unauthored — the
client's NodePlayerAnimation defaults absent Speed to 1f, so the packets are
client-identical.

Run: python test_port_parity.py
"""
import json
import pathlib
import random

import combat
import forge
import seed
from combat_engine.state import _states as _engine_states

REPORT = pathlib.Path(__file__).resolve().parent.parent / "docs" / "combat-engine" \
    / "port_parity_report.md"

UID, ALLY1, ALLY2 = 11, 12, 13
STATS = {"ap": 31.0, "sp": 28.0, "tcr": 0.05, "scm": 1.5, "tha": 0.99}


def reset(area, mons):
    """Both paths start from THIS exact world: same monsters/HP, same player
    stats, empty pools/auras/aspects on either engine."""
    for d in (combat._mon, combat._maxhp, combat._moninfo, combat._last,
              combat._aggro, combat._php, combat._pmax, combat._power,
              combat._rp, combat._resource_model, combat._class_mana,
              combat._rp_max, combat._active_aspect, combat._conv_cast_stacks,
              combat._conv_last_cast, combat._conv_next_decay, combat._auras,
              combat._stun, combat._cast_last):
        d.clear()
    _engine_states.clear()
    for uid in (UID, ALLY1, ALLY2):
        combat.register_player(uid, 1337)
    combat.set_power(UID, STATS)
    combat.set_resource_model(UID, "conviction", 50)
    for ts, hp in mons:
        combat.register_monster(area, ts, hp, mon_id=100, frame="Enter", level=5)


def norm(attack):
    """Client-identical normalization (see module docstring)."""
    a = json.loads(json.dumps(attack))
    for n in a["Nodes"]:
        if n.get("Name") == "PlayerAnimation" and "Speed" not in n:
            n["Speed"] = 1.0
    return a


def run_sequence(area, mons, skills, rules, casts, path):
    """Run `casts` [(slot, target, rp_override|None), ...] through one path.
    Returns the list of (normalized) Attack packets."""
    reset(area, mons)
    graphs = {}
    for slot, skill_id, _name, _icon, _desc, node_list in skills:
        graphs[slot] = (skill_id, *forge.linear_graph(node_list))
    out = []
    for i, (slot, target, rp_override) in enumerate(casts):
        if rp_override is not None:
            combat._rp[UID] = rp_override
        skill_id, data, fg = graphs[slot]
        random.seed(4000 + i)
        allies = [f"p:{ALLY1}", f"p:{ALLY2}"]
        if path == "python":
            combat.set_class_rules(UID, None)            # force the Python route
            attack, killed, dmg = combat.cast_skill(
                area, UID, slot, target, data, fg, skill_id=skill_id, allies=allies)
        else:
            # exercise the REAL cutover route the live server takes — register the
            # class's rules and let begin_cast dispatch — not the engine directly
            combat.set_class_rules(UID, rules)
            pkts, killed, dmg = combat.begin_cast(
                area, UID, slot, target, data, fg, skill_id, allies)
            assert len(pkts) == 1, f"cast produced {len(pkts)} packets, expected 1"
            attack = pkts[0]
        out.append((skill_id, combat._rp.get(UID, 0), norm(attack), killed, dmg))
    return out


def diff_class(title, area, mons, skills, rules, casts, lines):
    py = run_sequence(area, mons, skills, rules, casts, "python")
    dt = run_sequence(area, mons, skills, rules, casts, "data")
    fails = 0
    lines.append(f"\n## {title}\n")
    lines.append("| # | skill | rp after | nodes | result |")
    lines.append("|---|-------|----------|-------|--------|")
    for i, ((sid_p, rp_p, atk_p, k_p, d_p), (sid_d, rp_d, atk_d, k_d, d_d)) \
            in enumerate(zip(py, dt)):
        ok = atk_p == atk_d and rp_p == rp_d and k_p == k_d and d_p == d_d
        names = ",".join(n["Name"] for n in atk_p["Nodes"])
        lines.append(f"| {i} | {sid_p} | {rp_p} | {len(atk_p['Nodes'])} | "
                     f"{'MATCH' if ok else '**DIFF**'} |")
        if not ok:
            fails += 1
            print(f"DIFF {title} cast#{i} skill {sid_p} (nodes: {names})")
            print("  python:", json.dumps(atk_p, sort_keys=True)[:600])
            print("  data  :", json.dumps(atk_d, sort_keys=True)[:600])
            if (rp_p, k_p, d_p) != (rp_d, k_d, d_d):
                print(f"  rp {rp_p}/{rp_d} killed {k_p}/{k_d} dmg {d_p}/{d_d}")
    return fails


# the representative sequences: builders at several stack levels, both Meteor
# aspect branches, the spenders full AND empty, auras/lifelink with a party
PALADIN_CASTS = [
    (0, "m:1", None),        # auto (+3)
    (1, "m:1", None),        # Vow at 3 stacks
    (1, "m:1", None),        # Vow at 5
    (2, "m:1", None),        # Meteor, default warrior branch (single, 1.5x)
    (3, "m:1", None),        # Protection -> healer aspect + party guard
    (2, "m:1", None),        # Meteor, healer branch (AoE cap 4 + Suppression)
    (4, "m:2", 50),          # Smite at 50 (spend all + lifelink + warrior aspect)
    (2, "m:2", None),        # Meteor, back to warrior branch (Burning Field)
    (4, "m:2", None),        # Smite at 0 (no stacks: 1x, lifelink still)
    (0, "m:2", None),        # auto again
]

VOID_CASTS = [
    (0, "m:1", None),        # Rend (+3)
    (1, "m:1", None),        # Siphon at 3 (+2%/stack, 35% lifelink)
    (2, "m:1", None),        # Maw at 5 (Umbral Rot on the victim)
    (3, "m:1", None),        # Event Horizon (party guard + AuraVFX)
    (4, "m:1", 37),          # Manifest at 37 (spend all, Shadow Form morph)
    (1, "m:1", None),        # Siphon at 0
]


def test_seeded_rules():
    """The rule configs actually land in classes.raw via seed_paladin/seed_void."""
    import db
    db.use_throwaway()
    seed.run()
    with db.connect() as conn:
        for cid, rules in ((seed.PALADIN_CLASS_ID, seed.PALADIN_RULES),
                           (seed.VOID_CLASS_ID, seed.VOID_RULES)):
            row = conn.execute("SELECT raw FROM classes WHERE class_id=?",
                               (cid,)).fetchone()
            raw = json.loads(row["raw"])
            assert raw.get("rules") == rules, f"class {cid} rules not seeded"
    print("seed OK: PALADIN_RULES/VOID_RULES stored in classes.raw")


def main():
    lines = ["# Port parity report — Conviction/Hunger as data vs combat.py",
             "",
             "Same casts, same seeds, same `combat._hit` rolls on both paths;",
             "every Attack packet, post-cast pool, kill list and damage total",
             "compared. Normalization applied: `PlayerAnimation` without a",
             "`Speed` key equals `Speed: 1.0` (the client's NodePlayerAnimation",
             "default) — the engine omits unauthored optional keys, matching",
             "the captured AE wire shape."]
    mons = [("m:1", 4000), ("m:2", 4000), ("m:3", 3000), ("m:4", 3000),
            ("m:5", 3000), ("m:6", 2500)]
    fails = 0
    fails += diff_class("Paladin (Reduxidain 69420) — Conviction", "pal", mons,
                        seed._PALADIN_SKILLS, seed.PALADIN_RULES, PALADIN_CASTS, lines)
    fails += diff_class("Voidwalker (2064) — Hunger", "void", mons,
                        seed._VOID_SKILLS, seed.VOID_RULES, VOID_CASTS, lines)
    total = len(PALADIN_CASTS) + len(VOID_CASTS)
    lines.append(f"\n**{total - fails}/{total} casts identical**"
                 + ("" if fails == 0 else f" — {fails} DIFF"))
    REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"report written: {REPORT}")
    assert fails == 0, f"{fails} casts diverged between the paths"
    test_seeded_rules()
    print(f"ALL PORT PARITY TESTS PASSED ({total} casts identical on both paths)")


if __name__ == "__main__":
    main()
