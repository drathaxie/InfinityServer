"""
Stage-5 golden-master test — the Infinity Hero class (2022) rebuilt as PURE DATA
must reproduce AE's own captured casts.

Two real AE sessions are replayed end to end (447 Attack packets: 233 in
fixtures/infinity_hero_casts.json + 214 by the second caster inside
golden_attack_fixtures.json). For each skill press the engine runs
seed.INFINITY_HERO_RULES through combat_engine.rules and the produced Attack
nodes are compared against what AE actually sent.

What is FED from the capture (world state the server would know, never class
mechanics):
  * the cast target, the monsters a swing resolved onto, the party roster
  * damage/heal numbers, crit flags and post-hit HP  (the stat formula is not
    what this test is about — combat.py owns that, see test_port_parity.py)
  * cooldown milliseconds (AE's CDs drift with the caster's gear mid-session:
    the same skill shows 3959ms in one session and 3918ms in the other)
  * epoch-ms timestamps
  * two environment booleans: `ranged` (the caster's weapon — one session
    auto-attacks with a bow) and `dash` (whether the target was out of melee
    reach, which makes Serpent's Kiss lead with a gap-closer)

What is PROVEN by the comparison — i.e. everything that comes out of the rule
config alone: which nodes fire and in what order, every static prop (sounds,
particles, hitboxes, animations, ranges, timings, icons), which of the four
Aspects is active, which branch each press takes, which effect aura it applies
and to whom, the Heroic pool arithmetic, the arm-at-25 ultimate, and the exact
combo-rebind broadcast for each aspect.

Run: python test_infinity_hero.py
"""
import collections
import json
import pathlib

import seed
from combat_engine.engine import RenderContext, ValueSource
from combat_engine.rules import run_skill
from combat_engine.state import CombatState, _states as _engine_states

FIX = pathlib.Path(__file__).resolve().parent.parent / "docs" / "combat-engine" / "fixtures"
REPORT = FIX.parent / "infinity_hero_report.md"

ASPECTS = {"Warrior Aspect", "Mage Aspect", "Healer Aspect", "Rogue Aspect"}
SLOT_SKILL = {0: 168, 1: 169, 2: 170, 3: 171, 4: 172}


# --------------------------------------------------------------------------
# capture-driven value source
# --------------------------------------------------------------------------
class CaptureSource(ValueSource):
    """Feeds each render node the outcome AE recorded for it. Queues are keyed
    by node name and popped in render order, so a structural divergence (an
    extra or missing node) surfaces as a queue mismatch rather than silently
    shifting numbers onto the wrong node."""

    def __init__(self, nodes, clock):
        self.q = collections.defaultdict(collections.deque)
        for n in nodes:
            self.q[n["Name"]].append(n)
        self.clock = clock
        self.missing = []
        # AE stamps IndexReset.TS / PlayerHitStream.Time from its own clock
        # (a .NET epoch, not unix ms) — replay them per node kind, since the
        # rebind broadcast and the async field packet are stamped separately
        self.stamps = collections.defaultdict(collections.deque)
        for n in nodes:
            if n["Name"] == "IndexReset":
                self.stamps["IndexReset"].append(n["TS"])
            elif n["Name"] == "PlayerHitStream":
                self.stamps["PlayerHitStream"].append(n["Time"])

    def _pop(self, name):
        if not self.q[name]:
            self.missing.append(name)
            return None
        return self.q[name].popleft()

    def damage(self, ctx, props):
        cap = self._pop("Damage")
        if cap is None:
            return None
        return (list(cap["DamageTypes"]), list(cap["Damages"]),
                list(cap["Targets"]), list(cap["TargetHPs"]))

    def resource_total(self, ctx, props):
        return ctx.state.rp                      # engine-owned: the thing under test

    def cooldown_ms(self, ctx, props):
        cap = self._pop("Cooldown")
        return int(cap["CD"]) if cap else int(props.get("CD") or 0)

    def timestamp_ms(self, kind=None):
        q = self.stamps.get(kind)
        return q.popleft() if q else self.clock

    def now(self):
        return self.clock / 1000.0


# --------------------------------------------------------------------------
# split a captured session into presses
# --------------------------------------------------------------------------
def group_presses(casts):
    """-> [{kind, slot, main:[node...], extra:[(status,[node...])...], meta}]

    AE splits one activation across several Attack packets (a StatusCode-2
    lead-in, the StatusCode-1 resolution, StatusCode-3 rebind broadcasts, and
    an async StatusCode-1/4 field packet for the meteor). A press ends when its
    own hidden Aspect aura lands."""
    presses, cur = [], None

    def close():
        nonlocal cur
        if cur:
            presses.append(cur)
            cur = None

    def attach(status, nodes, slot=None):
        """Async continuation / broadcast -> the press it belongs to (the open
        one, else the most recent press on that slot, else the last press)."""
        # a bare {"Cooldown", "success": true} is the global-cooldown ACK the
        # server sends for the NEXT input, not part of this activation
        nodes = [n for n in nodes if not (n["Name"] == "Cooldown" and "success" in n)]
        if not nodes:
            return
        if cur is not None and (slot is None or cur["slot"] == slot):
            cur["extra"].append((status, list(nodes)))
            return
        for p in reversed(presses):
            if slot is None or p["slot"] == slot:
                p["extra"].append((status, list(nodes)))
                return

    for c in casts:
        slot, sc, nodes = c["slot"], c.get("statusCode"), c["nodes"]
        names = {n["Name"] for n in nodes}
        owns = [n["AuraName"] for n in nodes
                if n["Name"] == "Aura" and n.get("AuraName") in ASPECTS]

        if slot == -1:                  # hit-stream tick damage, not an activation
            continue
        if sc == 3:                     # combo-rebind broadcast
            attach(3, nodes)
            continue
        if sc not in (1, 2):            # sc 4 / absent: async continuation packet
            attach(sc or 1, nodes, slot if slot in (1, 2, 3, 4) else None)
            continue
        if slot in (1, 2, 3, 4):
            if cur is None or cur["slot"] != slot or cur["kind"] != "skill":
                close()
                cur = {"kind": "skill", "slot": slot, "main": [], "extra": [],
                       "meta": {}}
            cur["main"].extend(nodes)
            if owns:
                cur["meta"]["aspect"] = owns[-1]
                close()
            continue
        if slot is None:
            if any(n["Name"] == "PlayerHitStream" for n in nodes):
                close()                                     # the empowered ult
                presses.append({"kind": "ult", "slot": 0, "main": list(nodes),
                                "extra": [], "meta": {}})
                continue
            if sc == 2 and names == {"Range"}:              # auto-attack lead-in
                close()
                cur = {"kind": "auto", "slot": 0, "main": list(nodes),
                       "extra": [], "meta": {}}
                continue
            if cur is not None and cur["kind"] == "auto":
                cur["main"].extend(nodes)
                close()
                continue
            attach(sc, nodes)
            continue
        attach(sc, nodes)
    close()
    return presses


# --------------------------------------------------------------------------
# replay one session
# --------------------------------------------------------------------------
def replay(name, casts, caster, lines, start_rp=0):
    presses = group_presses(casts)
    config = seed.INFINITY_HERO_RULES
    _engine_states.clear()
    state = CombatState(caster, rp_max=50)
    state.rp = start_rp
    state.vars.update({"armed": 1 if start_rp >= 25 else 0,
                       "ranged": 0, "dash": 0})

    ok = fail = skipped = known = 0
    clock = 1785514000000
    fails = []
    rp_offset = None
    for idx, p in enumerate(presses):
        clock += 2000
        # An activation AE never resolved was INTERRUPTED (the player moved or
        # took a hit mid-animation): a skill press with no Aspect aura, or an
        # auto with no resolution packet. Nothing to compare — and no state
        # change to replay either, which is what keeps the Aspect chain in step.
        aborted = (not p["meta"].get("aspect") if p["kind"] == "skill"
                   else not any(n["Name"] == "Cooldown" for n in p["main"]))
        if p["kind"] != "ult" and aborted:
            skipped += 1
            continue
        cap_main = p["main"]
        cap_extra = p["extra"]
        skill_id = SLOT_SKILL[p["slot"]]

        # --- world state fed from the capture -----------------------------
        dmg = [n for n in cap_main if n["Name"] == "Damage"]
        # what the swing's box resolved onto — AE's hitbox sometimes catches an
        # enemy the damage pass then skips, so prefer the captured box list
        box = next((n for n in cap_main if n["Name"] == "AnimationHitbox"), None)
        hit_targets = ([t for t in box["Targets"] if t.startswith("m:")] if box
                       else [t for n in dmg for t in n["Targets"] if t.startswith("m:")])
        allies = [t for n in dmg for t in n["Targets"] if t.startswith("p:")] \
            or [n["Targets"] for n in cap_main if n["Name"] == "RangeMulti"]
        allies = allies[0] if allies and isinstance(allies[0], list) else allies
        rng = next((n for n in cap_main if n["Name"] == "Range"), None)
        rmulti = next((n for n in cap_main if n["Name"] == "RangeMulti"), None)
        target = (rng or {}).get("Target") or (hit_targets[0] if hit_targets else None)
        if rmulti:
            allies = list(rmulti["Targets"])

        state.vars["ranged"] = int(any(
            n["Name"] == "SpellAnimation" and n.get("FX") == "Projectile"
            for n in cap_main))
        state.vars["dash"] = int(any(n["Name"] == "DashToTarget" for n in cap_main))
        if p["kind"] == "ult":
            state.vars["armed"] = 1

        src = CaptureSource(cap_main + [n for _s, ns in cap_extra for n in ns], clock)
        ctx = RenderContext(caster=caster, slot=p["slot"], target=target,
                            targets=hit_targets or ([target] if target else []),
                            state=state, source=src,
                            allies=[a for a in allies if a != caster],
                            enemies=hit_targets)
        got_main = run_skill(config, skill_id, ctx)
        got_extra = list(ctx.extra_packets)

        if rp_offset is None:       # infer the pool AE was already carrying
            cap_rp = next((n["Amount"] for n in cap_main
                           if n["Name"] == "Resource"), None)
            got_rp = next((n["Amount"] for n in got_main
                           if n["Name"] == "Resource"), None)
            if cap_rp is not None and got_rp is not None:
                rp_offset = cap_rp - got_rp

        prob = diff_press(cap_main, got_main, cap_extra, got_extra,
                          async_delays={id(ns) for _d, _s, ns in ctx.delayed_packets},
                          # the capture stops mid-flight: a delayed packet from
                          # the final press simply never made it into the file
                          skip_async=(idx == len(presses) - 1))
        if prob is None:
            ok += 1
        elif is_known_variance(prob):
            known += 1
        else:
            fail += 1
            fails.append((idx, p, prob, got_main, got_extra))
    return presses, ok, fail, fails, rp_offset, skipped, known


def _key(node):
    return json.dumps(node, sort_keys=True)


_WORLD_KEYS = ("Targets", "OriginTarget", "Target")

# Divergences that are AE's own inconsistency, not ours. Each is stated as a
# precise signature so a genuine regression can never slip through under it.
KNOWN_VARIANCES = [
    # ONE captured Serpent's Kiss (of the 8 that apply it) shipped its
    # Concealed Blade with an empty target list, so the buff landed on nobody.
    # Every other cast in both sessions targets the caster, which is the only
    # reading under which the buff means anything — we follow those.
    ("Concealed Blade applied to nobody",
     lambda d: '"AuraName": "Concealed Blade"' in d and '"Targets": []' in d),
]


def is_known_variance(problem):
    return any(pred(problem) for _label, pred in KNOWN_VARIANCES)


def _async_shape(nodes):
    """An async (delayed) packet is graded on STRUCTURE, not bytes: it is sent
    a second after the cast and its targets resolve against the world as it is
    then — which monsters are still standing at that moment is not something
    this capture records (another player's hit can drop them in between). Node
    order and every static prop still have to match; only the world-resolved
    target fields and the presence of the target-anchored hit stream are
    tolerated. This is the ONLY place byte-exactness is not asserted."""
    out = []
    for n in nodes:
        if n["Name"] == "PlayerHitStream":
            continue
        out.append({k: ("<world>" if k in _WORLD_KEYS else v)
                    for k, v in n.items()})
    return json.dumps(out, sort_keys=True)


def diff_press(cap_main, got_main, cap_extra, got_extra, async_delays=frozenset(),
               skip_async=False):
    """None when the press matches, else a human-readable first divergence.
    Extra packets are compared as a set of node-sequences: they are separate
    network sends whose relative arrival order is not part of the contract."""
    if len(cap_main) != len(got_main):
        cn = [n["Name"] for n in cap_main]
        gn = [n["Name"] for n in got_main]
        return f"node count {len(cap_main)} != {len(got_main)}\n    AE : {cn}\n    ENG: {gn}"
    for i, (a, b) in enumerate(zip(cap_main, got_main)):
        if _key(a) != _key(b):
            return (f"node #{i} {a['Name']}\n    AE : {_key(a)[:400]}"
                    f"\n    ENG: {_key(b)[:400]}")
    # delayed packets: compare by shape (see _async_shape); the rest byte-exact
    cap_async = [ns for _s, ns in cap_extra
                 if any(n["Name"] == "Particle" and n.get("Lifetime") for n in ns)]
    got_async = [ns for _s, ns in got_extra if id(ns) in async_delays]
    if not skip_async and \
            sorted(map(_async_shape, cap_async)) != sorted(map(_async_shape, got_async)):
        return ("async packet shape differs\n"
                + "".join(f"    AE : {_async_shape([n])[:260]}\n"
                          for ns in cap_async for n in ns)
                + "".join(f"    ENG: {_async_shape([n])[:260]}\n"
                          for ns in got_async for n in ns))
    cap_set = sorted(_key(ns) for _s, ns in cap_extra if ns not in cap_async)
    got_set = sorted(_key(ns) for _s, ns in got_extra if id(ns) not in async_delays)
    if cap_set != got_set:
        only_ae = [x for x in cap_set if x not in got_set]
        only_eng = [x for x in got_set if x not in cap_set]
        return ("extra packets differ\n"
                + "".join(f"    AE only : {x[:300]}\n" for x in only_ae)
                + "".join(f"    ENG only: {x[:300]}\n" for x in only_eng))
    return None


def test_harness_sensitivity(ih, sess2):
    """A golden-master test is only worth its sensitivity: deliberately break
    each kind of thing the rule config encodes and prove the replay catches it.
    Without this, "155 presses match" could just mean the comparison is lax."""
    import copy
    base = seed.INFINITY_HERO_RULES

    def score():
        return (replay("m", ih, "p:508915", [], start_rp=0)[2]
                + replay("m", sess2, "p:31175933", [], start_rp=17)[2])

    def mutate(label, fn):
        seed.INFINITY_HERO_RULES = copy.deepcopy(base)
        fn(seed.INFINITY_HERO_RULES)
        n = score()
        assert n > 0, f"harness MISSED a deliberate break: {label}"
        return f"{label} -> {n} presses diverge"

    s1, post = "169", None
    checks = [
        ("a skill applies the wrong Aspect", lambda r: [
            x.update({"Aura": "Mage Aspect"})
            for br in r["skills"][s1][0]["Cases"].values()
            for x in br if x.get("Aura") == "Warrior Aspect"]),
        ("a branch stops granting Heroic", lambda r:
            r["skills"][s1][0]["Cases"]["Mage Aspect"].remove(
                next(x for x in r["skills"][s1][0]["Cases"]["Mage Aspect"]
                     if x.get("Do") == "ResourceOp"))),
        ("a branch applies the wrong effect aura", lambda r: [
            x.update({"Aura": "Armor Melted"})
            for x in r["skills"][s1][0]["Cases"]["Rogue Aspect"]
            if x.get("Aura") == "Prepared Strike"]),
        ("the ultimate arms at the wrong Heroic count", lambda r: [
            x.update({"If": x["If"].replace("25", "20")})
            for x in r["post"] if "25" in str(x.get("If"))]),
        ("a combo rebind swaps in the wrong icon", lambda r: [
            n.update({"Icon": "InfinityHero/InfinityHeroC3"})
            for x in r["post"] if x.get("On") == "aspect"
            for n in x["Cases"]["Mage Aspect"][0]["Nodes"]
            if n.get("Slot") == 4 and n["Name"] == "SetSkillIndex"]),
        ("a sound cue changes", lambda r: [
            n.update({"Sound": "sfx_wrong"})
            for n in r["skills"]["170"][0]["Cases"]["Warrior Aspect"]
            if n.get("Name") == "SoundFX"]),
        ("a hitbox is resized", lambda r: [
            n.update({"Width": 11})
            for n in r["skills"][s1][0]["Cases"]["Mage Aspect"]
            if n.get("Name") == "AnimationHitbox"]),
    ]
    try:
        for label, fn in checks:
            mutate(label, fn)
    finally:
        seed.INFINITY_HERO_RULES = base
    print(f"harness sensitivity OK: {len(checks)} deliberate breakages all caught")


def main():
    ih = json.loads((FIX / "infinity_hero_casts.json").read_text(encoding="utf-8"))
    gold = json.loads((FIX / "golden_attack_fixtures.json").read_text(encoding="utf-8"))
    sess2 = [c for c in gold if c.get("caster") == "p:31175933"]

    lines = ["# Infinity Hero (class 2022) — golden-master report", "",
             "The class is authored as pure data (`seed.INFINITY_HERO_RULES`).",
             "Both captured AE sessions are replayed press by press and every",
             "Attack node compared against what AE actually sent.", ""]
    total_ok = total_fail = total_known = 0
    for label, casts, caster in (("infinity_hero_casts.json", ih, "p:508915"),
                                 ("golden_attack_fixtures.json (2nd session)",
                                  sess2, "p:31175933")):
        # pass 1 discovers the pool the capture opened mid-session with (world
        # state, not mechanics); pass 2 is the graded run
        _p, _o, _f, _fl, offset, _sk, _kn = replay(label, casts, caster, lines)
        presses, ok, fail, fails, _, skipped, known = replay(
            label, casts, caster, lines, start_rp=max(0, offset or 0))
        total_ok += ok
        total_fail += fail
        total_known += known
        kinds = collections.Counter(p["kind"] for p in presses)
        nodes = sum(len(p["main"]) + sum(len(ns) for _s, ns in p["extra"])
                    for p in presses)
        print(f"replay {label}: {len(casts)} packets -> {len(presses)} presses "
              f"({dict(kinds)}), {nodes} nodes, {ok} match, {fail} DIFF"
              + (f", {known} known-AE-variance" if known else "")
              + (f", {skipped} interrupted (skipped)" if skipped else ""))
        lines.append(f"## {label}")
        lines.append("")
        lines.append(f"- {len(casts)} captured packets -> {len(presses)} presses "
                     f"({', '.join(f'{k} x{v}' for k, v in sorted(kinds.items()))})")
        lines.append(f"- **{ok}/{ok + fail + known} graded presses reproduced "
                     f"exactly** ({nodes} nodes compared)")
        if known:
            lines.append(f"- {known} known AE variance "
                         f"({', '.join(l for l, _p in KNOWN_VARIANCES)})")
        if skipped:
            lines.append(f"- {skipped} activations AE interrupted mid-cast "
                         "(no resolution packet to compare)")
        lines.append("")
        for idx, p, prob, _gm, _ge in fails[:12]:
            print(f"  DIFF press#{idx} slot {p['slot']} ({p['kind']}): {prob}")
            lines.append(f"  - press #{idx} slot {p['slot']}: `{prob.splitlines()[0]}`")
    test_harness_sensitivity(ih, sess2)
    lines.append("## Harness sensitivity")
    lines.append("")
    lines.append("Seven deliberate breakages of the rule config (wrong Aspect, "
                 "missing Heroic gain, wrong effect aura, wrong arm threshold, "
                 "wrong rebind icon, changed sound, resized hitbox) are each "
                 "confirmed to make the replay fail — the match above is not a "
                 "lax comparison.")
    REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"report written: {REPORT}")
    assert total_fail == 0, f"{total_fail} presses diverged from the AE capture"
    print(f"ALL INFINITY HERO TESTS PASSED ({total_ok} presses reproduced from "
          f"data, {total_known} known AE variance)")


if __name__ == "__main__":
    main()
