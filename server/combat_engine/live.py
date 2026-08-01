"""
The LIVE bridge — run a data-authored cast against the real server combat
machinery (combat.py's stat rolls, monster HP, aura effects, resource pools).

This is the data-path twin of combat.cast_skill: same inputs, same Attack
output, but the class MECHANICS come from a rule config (rules.py) instead of
skill_id-keyed Python. During the port both paths coexist; test_port_parity.py
diffs them cast-for-cast to prove zero behavior change before any cutover.

Bridging rules (deliberate, documented):
  - the resource pool stays combat._rp (decay loops, hpmp packets and guard
    snapshots all read it) — the engine state syncs in at cast start and out
    at cast end;
  - damage/heal rolls call combat._hit/_raise_hp so stats, crits, weapon gems,
    Dragon's Bane and guard bonuses behave identically (and a seeded RNG
    produces identical numbers on both paths);
  - every emitted Aura node ALSO registers through combat.apply_aura, exactly
    like combat._render_node does, so DoT/guard server effects keep working.
"""
import time

import combat
from .engine import RenderContext, ValueSource, build_attack, walk_graph
from .rules import rule, run_skill, _amount
from .state import get_state


class LiveValueSource(ValueSource):
    """Server-computed values from the real combat state. The Damage branch is
    a faithful port of combat._render_node's offensive path: helper-resolved
    targets filtered to monsters, MaxTargets cap, per-target _hit roll (skill
    Multiplier x rule-layer MultScale), authoritative HP mutation, kill and
    hit tracking for the rule layer (@hits, dmg_total, _killed)."""

    def damage(self, ctx, props):
        if props.get("Heal"):
            return self._heal(ctx, props)
        mult = float(props.get("Multiplier") or 0) or 1.0
        mult *= float(props.get("MultScale") or 1.0)
        magical = props.get("DamageType") == "Magical"
        guaranteed = bool(props.get("Guaranteed"))
        tgts = ctx.resolve_targets(props, default=None)
        mtgts = [t for t in tgts if isinstance(t, str) and t.startswith("m:")]
        try:
            cap = int(props.get("MaxTargets")) if props.get("MaxTargets") is not None else None
        except (TypeError, ValueError):
            cap = None
        if cap is not None:
            mtgts = mtgts[:max(0, cap)]
        if not mtgts:
            return None
        area = ctx.area
        dtypes, damages, hps = [], [], []
        for ts in mtgts:
            d, dtype = combat._hit(ctx.caster, mult * combat._dragon_bonus(ctx.caster, area, ts),
                                   magical, guaranteed)
            key = (area, ts)
            prev = combat._mon.get(key, combat.DEFAULT_HP)
            hp = max(0, prev - d)
            combat._mon[key] = hp
            if prev > 0 and hp <= 0:
                ctx.vars.setdefault("_killed", []).append(ts)
            damages.append(d); hps.append(hp); dtypes.append(dtype)
            ctx.vars["dmg_total"] = ctx.vars.get("dmg_total", 0) + d
            # the aspect-effect target set: monsters this cast struck that are
            # still alive (mirrors combat._meteor_aspect_node's filter)
            if hp > 0 and ts not in (ctx.vars.get("_hits") or []):
                ctx.vars.setdefault("_hits", []).append(ts)
        return (dtypes, damages, mtgts, hps)

    def _heal(self, ctx, props):
        """A Damage node authored as a heal (negative popup raising ally HP),
        mirroring combat._render_node's Heal branch."""
        mult = float(props.get("Multiplier") or 0) or 1.0
        mult *= float(props.get("MultScale") or 1.0)
        cap = int(props.get("MaxTargets") or combat.HEAL_MAX_TARGETS)
        tgts = ctx.resolve_targets(props, default=None)
        if not any(isinstance(t, str) and t.startswith("p:") for t in tgts):
            tgts = ctx._ally_list()
        ptgts = [t for t in tgts if isinstance(t, str) and t.startswith("p:")][:cap]
        if not ptgts:
            return None
        dtypes, damages, hps = [], [], []
        for ts in ptgts:
            amt, crit = combat._heal_amount(ctx.caster, mult)
            hps.append(combat._raise_hp(combat._uid_of(ts), amt))
            damages.append(-amt); dtypes.append(1 if crit else 0)
        return (dtypes, damages, ptgts, hps)

    def resource_total(self, ctx, props):
        return ctx.state.rp


@rule("Heal")
def _r_heal(ctx, r, out):
    """Flat lifelink-style heal: raise each target's HP by Amount and emit the
    negative-Damages node (mirrors combat._lifelink_node)."""
    amt = int(_amount(r["Amount"], ctx))
    if amt <= 0:
        return
    tgts = ctx.resolve_targets({"Targets": r.get("Targets", "@allies")})
    if r.get("MaxTargets") is not None:
        tgts = tgts[:int(r["MaxTargets"])]
    ptgts = [t for t in tgts if isinstance(t, str) and t.startswith("p:")]
    if not ptgts:
        return
    node = {"Name": "Damage", "DamageTypes": [0] * len(ptgts),
            "Damages": [-amt] * len(ptgts), "Targets": ptgts,
            "TargetHPs": [combat._raise_hp(combat._uid_of(ts), amt) for ts in ptgts]}
    if r.get("Immediate"):
        node["Immediate"] = True
    out.append(node)


def cast_skill_data(area, uid, slot, target, data, forge, skill_id, rules_config,
                    allies=None, stats=None):
    """Data-path cast -> (attack, killed_list, total_dmg) — the same contract
    as combat.cast_skill, with mechanics from `rules_config` instead of the
    skill_id-keyed Python."""
    caster = f"p:{uid}"
    res_cfg = (rules_config or {}).get("resource") or {}
    state = get_state(caster, rp_max=int(res_cfg.get("max")
                                         or combat._rp_max.get(uid, combat.MAX_RP)))
    state.rp_max = int(res_cfg.get("max") or combat._rp_max.get(uid, combat.MAX_RP))
    state.rp = combat._rp.get(uid, 0)                   # bridge IN
    order, nodes = walk_graph(data or [], forge or [])
    ally_list = list(allies or [])
    ctx = RenderContext(caster=caster, slot=slot, target=target, nodes=nodes,
                        state=state, source=LiveValueSource(),
                        allies=lambda: ally_list,
                        enemies=lambda: combat.alive_monsters(area),
                        stats=stats)
    ctx.area = area
    ctx.graph_order = order
    ctx.vars["dmg_total"] = 0

    out = run_skill(rules_config or {}, skill_id, ctx)

    combat._rp[uid] = state.rp                          # bridge OUT
    if res_cfg.get("model", "stacking") in ("stacking", "conviction"):
        combat._conv_last_cast[uid] = time.time()       # decay idle timer parity
        # the post-cast pool is the LAST node so it's the final bar-set
        # instruction (combat.cast_skill's Smite "stacks didn't drop" fix)
        out = [n for n in out if n.get("Name") != "Resource"]
        out.append({"Name": "Resource", "Amount": state.rp})
    if not any(n["Name"] == "Cooldown" for n in out):
        out.append({"Name": "Cooldown", "Animation": "", "Slot": slot, "CD": 1500})
    # server-side aura effects, exactly like combat._render_node's Aura branch
    for n in out:
        if n.get("Name") == "Aura" and n.get("AuraName"):
            combat.apply_aura(area, n["AuraName"], n.get("Targets") or [], caster)
    attack = build_attack(caster, slot, out)
    return attack, list(ctx.vars.get("_killed") or []), ctx.vars.get("dmg_total", 0)
