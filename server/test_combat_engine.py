"""
Combat node engine — golden-master replay tests.

The 549 captured AE Attack packets (docs/combat-engine/fixtures) are the spec:
for every captured cast the engine must re-emit the exact Nodes array. Two
passes per cast:

  1. VERBATIM: each captured node is fed back through its renderer as authored
     props with a ReplayValueSource pinning the recorded outcome. The render
     must equal the capture exactly — this catches any schema drift (wrong key
     set, wrong coercion, invented fields).
  2. STRIPPED: server-computed and defaulted fields are removed from the
     authored input first (casterTS, default Charge/Hide/ReleaseMode,
     timestamps, ...). The renderer must REFILL them to the captured values —
     this proves the defaults are AE's, so a hand-authored graph (stage 5's
     Infinity Hero) renders the same wire shape without spelling every key.

Run: python test_combat_engine.py            (from server/, like the other tests)
"""
import json
import pathlib
import sys

import combat_engine
from combat_engine import RenderContext, ReplayValueSource, render_graph, build_attack
from combat_engine.nodes import RENDERERS, render_node
from combat_engine.state import CombatState, get_state, drop_state

FIXTURES = pathlib.Path(__file__).resolve().parent.parent / "docs" / "combat-engine" / "fixtures"


def load(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def derive_target(cast):
    """The cast's primary target, the way the server knew it: the Range node's
    Target, else the first hostile a Damage-ish node names."""
    for n in cast["nodes"]:
        if n["Name"] == "Range" and isinstance(n.get("Target"), str):
            return n["Target"]
    for n in cast["nodes"]:
        for t in (n.get("Targets") or []) if isinstance(n.get("Targets"), list) else []:
            if isinstance(t, str) and t.startswith("m:"):
                return t
    return None


def make_ctx(cast):
    caster = cast["caster"]
    drop_state(caster)
    return RenderContext(caster=caster, slot=cast["slot"] if cast["slot"] is not None else 0,
                         target=derive_target(cast), source=ReplayValueSource())


# --- pass 2: which authored fields the renderer must refill --------------------
# key -> default (or a callable(ctx, node) -> expected refill). A key is only
# stripped when the captured value EQUALS what the renderer would refill, so
# non-default captures stay authored (they'd be authored in a real graph too).
def _strippable(ctx, node):
    name = node["Name"]
    fixed = {
        "Cooldown": {"Animation": ""},
        "SetSkillIndex": {"hide": False, "Index": 0},
        "IndexReset": {"Stay": False, "CD": 0, "Index": 0, "TS": None},
        "Range": {"Charge": False, "HoldAtRange": False, "Target": ctx.target},
        "Aura": {"casterTS": ctx.caster, "uniquenessType": 1, "Animation": "",
                 "Hide": False, "Targets": [ctx.caster]},
        "Particle": {"Follow": "No Follow", "Targets": [ctx.caster]},
        "SoundFX": {"MinPitch": 0.0, "MaxPitch": 0.0, "Time": 0.0},
        "ImpactSoundFX": {"MinPitch": 0.0, "MaxPitch": 0.0},
        "Restrict": {"Direction": True, "ReleaseMode": "AtTime"},
        "UpdateAnimation": {"Tag": "combatIdle"},
        "SpellAnimation": {"AttachInit": "CastAttach", "Attach": "Cast",
                           "AttachImpact": "Origin", "target": ctx.target,
                           "Targets": 1},
        "PlayerAnimation": {"Priority": "Attack", "Targets": 1},
        "DashToTarget": {"Animation": "None", "Duration": 400,
                         "Target": ctx.target},
        "RangeMulti": {"Target": "Self"},
        "PlayerHitStream": {"Origin": "Target", "Interval": 1000, "Time": None,
                            "OriginTarget": ctx.target},
    }
    return fixed.get(name, {})


def strip_authored(ctx, node):
    """The authored version of a captured node: default/computed keys removed
    (only where the capture matches the refill). Returns (authored, ignore) —
    `ignore` = keys excluded from comparison (fresh timestamps)."""
    authored = dict(node)
    ignore = set()
    for key, default in _strippable(ctx, node).items():
        if key not in node:
            continue
        if default is None:                     # timestamp: strip + don't compare
            del authored[key]
            ignore.add(key)
        elif node[key] == default:
            del authored[key]
    return authored, ignore


def replay_file(name, require_all=False):
    """Replay every cast in a fixture file. Returns (casts, node_total, fails,
    uncovered) where uncovered counts nodes whose type has no renderer yet."""
    casts = load(name)
    node_total = fails = uncovered = 0
    fail_examples = []
    for ci, cast in enumerate(casts):
        ctx = make_ctx(cast)
        for ni, node in enumerate(cast["nodes"]):
            node_total += 1
            if node["Name"] not in RENDERERS:
                uncovered += 1
                if require_all:
                    fails += 1
                    fail_examples.append((ci, ni, node["Name"], "no renderer", None))
                continue
            # pass 1: verbatim
            got = render_node(ctx, dict(node))
            if got != node:
                fails += 1
                if len(fail_examples) < 8:
                    fail_examples.append((ci, ni, node["Name"], "verbatim", got))
                continue
            # pass 2: stripped -> refilled
            authored, ignore = strip_authored(ctx, node)
            got2 = render_node(ctx, authored)
            want = {k: v for k, v in node.items() if k not in ignore}
            got2cmp = {k: v for k, v in (got2 or {}).items() if k not in ignore}
            if got2cmp != want:
                fails += 1
                if len(fail_examples) < 8:
                    fail_examples.append((ci, ni, node["Name"], "stripped", got2))
    for ci, ni, name_, kind, got in fail_examples:
        cast = casts[ci]
        print(f"  FAIL [{kind}] cast#{ci} node#{ni} {name_}")
        print(f"    want: {json.dumps(cast['nodes'][ni], sort_keys=True)[:300]}")
        print(f"    got : {json.dumps(got, sort_keys=True)[:300]}")
    return len(casts), node_total, fails, uncovered


def test_state():
    st = CombatState("p:1", rp_max=50)
    assert st.gain_rp(30) == 30 and st.gain_rp(40) == 50, "rp caps at rp_max"
    assert st.spend_all_rp() == 50 and st.rp == 0
    st.set_aspect("Warrior Aspect", exclusive_group=["Warrior Aspect", "Mage Aspect"])
    st.set_aspect("Mage Aspect", exclusive_group=["Warrior Aspect", "Mage Aspect"])
    assert not st.aspect_active("Warrior Aspect") and st.aspect_active("Mage Aspect"), \
        "aspects in an exclusive group replace each other"
    assert st.active_aspect(["Warrior Aspect", "Mage Aspect"]) == "Mage Aspect"
    st.apply_aura("Armor Melted", 10.0, mods={"dmg_taken_mult": 0.20}, now=100.0)
    assert st.aura("Armor Melted", now=105.0) is not None
    assert st.modifier("dmg_taken_mult", now=105.0) == 0.20
    assert st.aura("Armor Melted", now=111.0) is None, "expired auras drop on read"
    st.set_combo_index(1, 2)
    assert st.combo_index(1) == 2 and st.combo_index(2) == 0
    st.reset_combo()
    assert st.combo_index(1) == 0
    # registry: same key -> same object; drop forgets
    assert get_state("p:9") is get_state("p:9")
    drop_state("p:9")
    print("state OK: rp cap/spend, exclusive aspects, aura expiry+mods, combo")


def test_graph_walk():
    """A SkillForge-authored graph renders through the same pipeline (interop
    with forge.linear_graph's [data, forge] shape)."""
    import forge
    data, fg = forge.linear_graph([
        ("0", {"Name": "OnRequest"}),
        ("1", {"Name": "Cooldown", "CD": 2000}),
        ("2", {"Name": "PlayerAnimation", "Animation": "Attack1", "Priority": "Attack",
               "Speed": 1.0}),
        ("3", {"Name": "Damage", "DamageTypes": [0], "Damages": [42],
               "Targets": ["m:5"], "TargetHPs": [58]}),
    ])
    order, nodes = combat_engine.walk_graph(data, fg)
    assert [p["Name"] for _i, p in order] == ["Cooldown", "PlayerAnimation", "Damage"]
    ctx = RenderContext(caster="p:1", slot=2, target="m:5", nodes=nodes,
                        source=ReplayValueSource())
    rendered = render_graph(order, ctx)
    assert rendered[0] == {"Name": "Cooldown", "Slot": 2, "CD": 2000, "Animation": ""}, \
        "Cooldown Slot must default to the cast slot"
    assert rendered[2]["Damages"] == [42] and rendered[2]["TargetHPs"] == [58]
    atk = build_attack("p:1", 2, rendered)
    assert atk["Cmd"] == "Attack" and atk["StatusCode"] == 1 and atk["Wait"] is True
    print("graph walk OK: linear_graph -> walk -> render -> Attack")


def main():
    test_state()
    test_graph_walk()
    total_casts = total_nodes = total_fails = total_uncovered = 0
    for name in ("golden_attack_fixtures.json", "infinity_hero_casts.json",
                 "monster_casts.json"):
        casts, nodes_, fails, uncovered = replay_file(name)
        total_casts += casts; total_nodes += nodes_
        total_fails += fails; total_uncovered += uncovered
        cov = nodes_ - uncovered
        print(f"replay {name}: {casts} casts, {cov}/{nodes_} nodes covered, "
              f"{fails} mismatches")
    assert total_fails == 0, f"{total_fails} replay mismatches"
    assert total_uncovered == 0, \
        f"{total_uncovered} nodes hit unimplemented renderers"
    print(f"ALL COMBAT ENGINE TESTS PASSED "
          f"({total_casts} casts, {total_nodes} nodes, verbatim + stripped)")


if __name__ == "__main__":
    sys.exit(main())
