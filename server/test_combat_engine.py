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


# every Node* class in docs/combat-engine/ae_node_semantics.cs (the 49 real types)
AE_NODE_TYPES = [
    "AnimationCancel", "AnimationHitbox", "Aura", "AuraVFX", "Channel",
    "ConditionalRange", "Cooldown", "Damage", "Dash", "DashToTarget",
    "DisableSkill", "DispenseDamage", "GlobalCooldown", "Hit", "HitStream",
    "HitTiles", "Hitbox", "ImpactAura", "ImpactSoundFX", "IndexReset",
    "InstantDamage", "Interruptable", "MaxSkillHold", "Message", "MonTransform",
    "MonsterMove", "MoveTargets", "Particle", "PlayerAnimation",
    "PlayerHitStream", "Range", "RangeMulti", "Resource", "Restrict",
    "RestrictRelease", "SetSkillIndex", "SkillGlow", "SoundFX", "SpawnPickup",
    "SpellAnimation", "StopChannel", "SwapSkill", "TileCluster", "TileMove",
    "TileSafe", "TileTrack", "TileWave", "UpdateAnimation", "UpdateIcon",
]


def test_full_vocabulary():
    """All 49 AE node types have a renderer, and each of the fixture-less ones
    emits the key set its decompiled Execute/Input body reads."""
    assert len(AE_NODE_TYPES) == 49
    missing = [n for n in AE_NODE_TYPES if n not in RENDERERS]
    assert not missing, f"unimplemented node types: {missing}"
    ctx = RenderContext(caster="m:429", slot=1, target="p:7",
                        source=ReplayValueSource())
    # (authored props, expected resolved node) — expectations read from the
    # matching Node* body in ae_node_semantics.cs
    cases = [
        ({"Name": "AnimationCancel"}, {"Name": "AnimationCancel"}),
        ({"Name": "AuraVFX", "AuraName": "Event Horizon", "VFX": "classMage_MageShield"},
         {"Name": "AuraVFX", "AuraName": "Event Horizon", "VFX": "classMage_MageShield"}),
        ({"Name": "ImpactAura", "AuraName": "Armor Melted", "SpellImpact": "IH_Impact"},
         {"Name": "ImpactAura", "AuraName": "Armor Melted", "SpellImpact": "IH_Impact"}),
        ({"Name": "Channel"}, {"Name": "Channel"}),
        ({"Name": "StopChannel"}, {"Name": "StopChannel"}),
        ({"Name": "Hit", "Animation": "Attack1", "Time": 0.3},
         {"Name": "Hit", "Animation": "Attack1", "Time": 0.3}),
        ({"Name": "Hitbox", "X": 2.0, "Y": 0.0, "Width": 6.0, "Height": 2.0},
         {"Name": "Hitbox", "X": 2.0, "Y": 0.0, "Width": 6.0, "Height": 2.0}),
        ({"Name": "Dash", "OffsetX": 3.0},
         {"Name": "Dash", "Duration": 400, "OffsetX": 3.0}),
        ({"Name": "MoveTargets", "Targets": ["p:7", "p:8"], "OffsetX": 1.0,
          "Duration": 300},
         {"Name": "MoveTargets", "Targets": "p:7,p:8", "OffsetX": 1.0,
          "Duration": 300}),
        ({"Name": "DisableSkill", "Slot": 3},
         {"Name": "DisableSkill", "Slot": 3, "Disabled": True}),
        ({"Name": "SkillGlow", "Slot": 2, "Active": False},
         {"Name": "SkillGlow", "Slot": 2, "Active": False}),
        ({"Name": "UpdateIcon", "Slot": 1, "Icons": "InfinityHero/InfinityHeroA1"},
         {"Name": "UpdateIcon", "Slot": 1, "Icons": "InfinityHero/InfinityHeroA1"}),
        ({"Name": "SwapSkill", "Slot": 4, "Skill": {"id": 172}},
         {"Name": "SwapSkill", "Slot": 4, "Skill": {"id": 172}}),
        ({"Name": "MaxSkillHold", "Slot": 1, "Time": 2500},
         {"Name": "MaxSkillHold", "Slot": 1, "Time": 2500}),
        ({"Name": "GlobalCooldown", "CD": [1000, -1, 2000]},
         {"Name": "GlobalCooldown", "CD": [1000, -1, 2000]}),
        ({"Name": "Message", "Title": "Hm", "Text": "..."},
         {"Name": "Message", "Title": "Hm", "Text": "..."}),
        ({"Name": "MonTransform", "detransform": True},
         {"Name": "MonTransform", "detransform": True}),
        ({"Name": "MonTransform", "Bundle": {"ID": 66126}, "Linkage": "monster-X",
          "Scale": 1.5},
         {"Name": "MonTransform", "Bundle": {"ID": 66126}, "Linkage": "monster-X",
          "Scale": 1.5}),
        ({"Name": "MonsterMove", "destX": 4.0, "destY": -1.0, "speed": 2.0},
         {"Name": "MonsterMove", "destX": 4.0, "destY": -1.0, "speed": 2.0}),
        ({"Name": "SpawnPickup", "PickupId": 9, "SpawnOffsetX": 1.0,
          "SpawnOffsetY": 0.0},
         {"Name": "SpawnPickup", "PickupId": 9, "SpawnOffsetX": 1.0,
          "SpawnOffsetY": 0.0}),
        ({"Name": "ConditionalRange", "hrange": 8.0, "vrange": 2.0},
         {"Name": "ConditionalRange", "hrange": 8.0, "vrange": 2.0,
          "type": "Hostile"}),
        ({"Name": "RestrictRelease"}, {"Name": "RestrictRelease"}),
        ({"Name": "RestrictRelease", "Animation": "DS Skill1C"},
         {"Name": "RestrictRelease", "Animation": "DS Skill1C"}),
        # tile telegraphs (MonReq Response payloads)
        ({"Name": "HitTiles", "Shape": "VerticalRectangle", "Speed": 1.2,
          "ScaleX": 2.0, "ScaleY": 8.0, "CastAnimation": "Castcharge"},
         {"Name": "HitTiles", "Shape": "VerticalRectangle", "Speed": 1.2,
          "ScaleX": 2.0, "ScaleY": 8.0, "CastAnimation": "Castcharge"}),
        ({"Name": "TileWave", "Speed": 0.8, "ImpactSound": "SFX_Wave"},
         {"Name": "TileWave", "Speed": 0.8, "ImpactSound": "SFX_Wave"}),
        ({"Name": "TileCluster", "Speed": 1.0, "ScaleX": 2.0, "ScaleY": 2.0,
          "ClusterOffsets": [0, 0, 1, 1, 2, 2, 3, 3, 4, 4, 5, 5, 6, 6, 7, 7]},
         {"Name": "TileCluster", "Speed": 1.0, "ScaleX": 2.0, "ScaleY": 2.0,
          "ClusterOffsets": [0, 0, 1, 1, 2, 2, 3, 3, 4, 4, 5, 5, 6, 6, 7, 7]}),
        ({"Name": "TileMove", "Speed": 1.0},
         {"Name": "TileMove", "Speed": 1.0}),
        ({"Name": "TileSafe", "Speed": 1.0, "ScaleX": 3.0, "ScaleY": 3.0,
          "SafeOffsetX": 5.0, "SafeOffsetY": 0.0},
         {"Name": "TileSafe", "Speed": 1.0, "ScaleX": 3.0, "ScaleY": 3.0,
          "SafeOffsetX": 5.0, "SafeOffsetY": 0.0}),
        ({"Name": "TileTrack", "Track": "Center", "Shape": "Circle",
          "Speed": 1.0, "ScaleX": 4.0, "ScaleY": 4.0},
         {"Name": "TileTrack", "Track": "Center", "Shape": "Circle",
          "Speed": 1.0, "ScaleX": 4.0, "ScaleY": 4.0}),
        ({"Name": "HitStream", "PosX": -6.0, "PosY": 0.0, "Speed": 1.0,
          "ScaleX": 2.0, "ScaleY": 12.0, "Time": 1785514131321,
          "Duration": 15000, "VFX": "FireWall"},
         {"Name": "HitStream", "PosX": -6.0, "PosY": 0.0, "Speed": 1.0,
          "ScaleX": 2.0, "ScaleY": 12.0, "Time": 1785514131321,
          "Duration": 15000, "VFX": "FireWall"}),
    ]
    for authored, want in cases:
        got = render_node(ctx, authored)
        assert got == want, f"{authored['Name']}:\n want {want}\n got  {got}"
    # a fresh HitStream/IndexReset stamps its own server timestamp
    hs = render_node(ctx, {"Name": "HitStream", "Duration": 1000})
    assert isinstance(hs["Time"], int) and hs["Time"] > 10 ** 12, \
        "HitStream must stamp epoch-ms when unauthored"
    print(f"vocabulary OK: {len(RENDERERS)}/49 AE node types registered, "
          f"{len(cases)} schema cases")


def test_safe_eval():
    from combat_engine.rules import safe_eval
    env = {"STR": 40, "INT": 10, "DEX": 20, "WIS": 5, "rp": 30, "spent": 25,
           "aura": "Armor Melted"}
    assert safe_eval("0.6*STR + 0.4*DEX", env) == 0.6 * 40 + 0.4 * 20
    assert safe_eval("1 + 0.05*spent", env) == 2.25
    assert safe_eval("rp >= 25 and STR > 10", env) is True
    assert safe_eval("min(rp, 25)", env) == 25
    assert safe_eval("2 if rp >= 50 else 1", env) == 1
    assert safe_eval("aura == 'Armor Melted'", env) is True
    for evil in ("__import__('os')", "().__class__", "STR.__add__(1)",
                 "[x for x in (1,)]", "lambda: 1", "open('x')",
                 "min(rp, key=int)", "env['STR']"):
        try:
            safe_eval(evil, env)
        except (ValueError, NameError, SyntaxError):
            continue
        raise AssertionError(f"evil expression evaluated: {evil}")
    print("safe_eval OK: arithmetic/branching/strings, hostile input rejected")


def test_rules():
    from combat_engine.rules import run_rules, run_skill
    from combat_engine.state import CombatState

    def ctx_for(state):
        c = RenderContext(caster="p:1", slot=1, target="m:5", source=ReplayValueSource(),
                          state=state, stats={"STR": 40, "INT": 10, "DEX": 20, "WIS": 5})
        return c

    # Formula -> var -> $-expression in a render node prop
    st = CombatState("p:1", rp_max=50)
    ctx = ctx_for(st)
    out = run_rules([
        {"Do": "Formula", "Var": "power", "Expr": "0.5*STR + 0.5*INT"},
        {"Do": "Emit", "Node": {"Name": "Resource", "Amount": {"$": "int(power)"}}},
    ], ctx)
    assert out == [{"Name": "Resource", "Amount": 25}]

    # ResourceOp gain/spend_all + threshold Branch
    st = CombatState("p:1", rp_max=50)
    ctx = ctx_for(st)
    seq = [
        {"Do": "Branch", "If": "rp >= 25",
         "Then": [{"Do": "ResourceOp", "Op": "spend_all"},
                  {"Do": "Emit", "Node": {"Name": "Cooldown", "Slot": 1,
                                          "CD": {"$": "1000 + spent*10"}}}],
         "Else": [{"Do": "ResourceOp", "Op": "gain", "Amount": 10}]},
    ]
    assert run_rules(seq, ctx) == [] and st.rp == 10, "below threshold: builds"
    st.rp = 30
    out = run_rules(seq, ctx_for(st))
    assert st.rp == 0 and out == [{"Name": "Cooldown", "Slot": 1, "CD": 1300,
                                   "Animation": ""}], "at threshold: spends all"

    # aspect Branch + SetAspect exclusivity
    st = CombatState("p:1", rp_max=50)
    ctx = ctx_for(st)
    st.set_aspect("Mage Aspect")
    out = run_rules([
        {"Do": "Branch", "On": "aspect",
         "Cases": {"Warrior Aspect": [{"Do": "Emit", "Node": {"Name": "Channel"}}],
                   "Mage Aspect": [{"Do": "Emit", "Node": {"Name": "StopChannel"}}]},
         "Default": []},
        {"Do": "SetAspect", "Aspect": "Warrior Aspect",
         "Group": ["Warrior Aspect", "Mage Aspect"]},
    ], ctx)
    assert out == [{"Name": "StopChannel"}], "branches on the PRE-cast aspect"
    assert st.aspect_active("Warrior Aspect") and not st.aspect_active("Mage Aspect")

    # ApplyAura: registry mods land on state, Aura node emitted, events fire
    # the class trigger (the Heroic +1-per-Aspect-Effect shape)
    st = CombatState("p:1", rp_max=50)
    ctx = ctx_for(st)
    config = {"triggers": {"aspect_effect": [
        {"Do": "ResourceOp", "Op": "gain", "Amount": 1}]},
        "skills": {"169": [
            {"Do": "ApplyAura", "Aura": "Armor Melted", "Targets": ["m:5"]},
            {"Name": "Resource", "Amount": {"$": "rp"}},
        ]}}
    nodes = run_skill(config, 169, ctx)
    assert nodes[0]["Name"] == "Aura" and nodes[0]["AuraName"] == "Armor Melted"
    assert st.rp == 1, "aspect_effect trigger granted +1 Heroic"
    assert nodes[1] == {"Name": "Resource", "Amount": 1}, \
        "Resource after the trigger sees the post-gain pool"
    from combat_engine.state import get_state, drop_state
    tstate = get_state("m:5")
    assert tstate.aura("Armor Melted") is not None
    assert tstate.modifier("dmg_taken_mult") == 0.20
    drop_state("m:5")

    # SetIndex drives combo state; inline Trigger registers for this cast
    st = CombatState("p:1", rp_max=50)
    ctx = ctx_for(st)
    run_rules([{"Do": "SetIndex", "Slot": 1, "Index": 2}], ctx)
    assert st.combo_index(1) == 2
    print("rules OK: Formula/$-props, threshold+aspect Branch, ResourceOp, "
          "ApplyAura->registry mods+events->trigger, SetIndex")


def main():
    test_state()
    test_graph_walk()
    test_full_vocabulary()
    test_safe_eval()
    test_rules()
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
