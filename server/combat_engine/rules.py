"""
The RULE layer — server-side data nodes that decide the numbers render nodes
carry. This is what makes a class's MECHANICS data: resource build/spend,
aspect branching, combo rebinds, formula damage — authored JSON, not Python.

## Authoring format (the class rule config, stored in classes.raw["rules"])

    {
      "resource": {"max": 50},
      "triggers": {"aspect_effect": [ <rule sequence> ]},
      "skills":   {"<skill_id>": [ <rule sequence> ], ...}
    }

A <rule sequence> is a list whose entries are either RENDER nodes ({"Name":
...} — rendered through nodes.py exactly as a graph node would be) or RULE
nodes ({"Do": ...} — executed server-side against the caster's CombatState):

    {"Do":"Formula",    "Var":"dmg", "Expr":"0.6*STR + 0.4*DEX"}
    {"Do":"Branch",     "If":"rp >= 25", "Then":[...], "Else":[...]}
    {"Do":"Branch",     "On":"aspect", "Cases":{"Warrior Aspect":[...]},
                        "Default":[...]}
    {"Do":"ResourceOp", "Op":"gain|spend|spend_all|set", "Amount": 1}
    {"Do":"SetIndex",   "Slot":1, "Index":2}
    {"Do":"SetAspect",  "Aspect":"Warrior Aspect", "Group":["...", ...]}
    {"Do":"ApplyAura",  "Aura":"Armor Melted", "Targets":["..."]|omitted}
    {"Do":"Trigger",    "On":"<event>", "Run":[...]}     (inline registration)
    {"Do":"Emit",       "Node":{"Name": ...}}            (explicit render)

Any prop value in a render node (or rule field marked expr-capable) may be
{"$": "<expr>"} — evaluated against the environment before use, so authored
graphs write {"Multiplier": {"$": "1 + 0.05*spent"}}.

## Expressions

Safe AST evaluation only — no eval(); names resolve from the environment:
caster stats (STR/INT/DEX/WIS/END/LCK, ap/sp), rp/rp_max, spent (what the
last spend_all consumed), combo (this slot's rebind index), slot, plus every
Formula-defined var. Operators: arithmetic, comparison, and/or/not, ternary;
functions: min/max/abs/round/int/float/floor/ceil.

## Events

fire_triggers(config, ctx, event) runs config["triggers"][event] (plus any
inline Trigger registrations from the current cast). ApplyAura fires
"aura_applied" and each event name the aura's registry entry lists under
"events" (e.g. the six Infinity Hero branch buffs list "aspect_effect", which
is what "+1 Heroic per Aspect Effect applied" hangs off). The host loop fires
"hit"/"kill" the same way.
"""
import ast
import math

from .nodes import render_node
from .auras import AURA_REGISTRY

_ALLOWED_FUNCS = {"min": min, "max": max, "abs": abs, "round": round,
                  "int": int, "float": float,
                  "floor": math.floor, "ceil": math.ceil}

_ALLOWED_NODES = (
    ast.Expression, ast.BinOp, ast.UnaryOp, ast.BoolOp, ast.Compare,
    ast.IfExp, ast.Call, ast.Name, ast.Constant, ast.Load,
    ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod, ast.Pow,
    ast.UAdd, ast.USub, ast.Not, ast.And, ast.Or,
    ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE,
)


def safe_eval(expr, env):
    """Evaluate a small arithmetic/boolean expression against env. Rejects
    anything outside the whitelist (attributes, subscripts, comprehensions,
    lambdas, imports — none of it parses through). Never eval()s."""
    tree = ast.parse(str(expr), mode="eval")
    for node in ast.walk(tree):
        if not isinstance(node, _ALLOWED_NODES):
            raise ValueError(f"disallowed expression element "
                             f"{type(node).__name__!r} in {expr!r}")
        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name) or node.func.id not in _ALLOWED_FUNCS:
                raise ValueError(f"disallowed call in {expr!r}")
            if node.keywords:
                raise ValueError(f"keyword arguments not allowed in {expr!r}")
        if isinstance(node, ast.Constant) and not isinstance(
                node.value, (int, float, bool, str, type(None))):
            raise ValueError(f"disallowed constant in {expr!r}")

    def ev(n):
        if isinstance(n, ast.Expression):
            return ev(n.body)
        if isinstance(n, ast.Constant):
            return n.value
        if isinstance(n, ast.Name):
            if n.id in env:
                return env[n.id]
            raise NameError(f"unknown name {n.id!r} in {expr!r}")
        if isinstance(n, ast.BinOp):
            a, b = ev(n.left), ev(n.right)
            op = type(n.op)
            return {ast.Add: lambda: a + b, ast.Sub: lambda: a - b,
                    ast.Mult: lambda: a * b, ast.Div: lambda: a / b,
                    ast.FloorDiv: lambda: a // b, ast.Mod: lambda: a % b,
                    ast.Pow: lambda: a ** b}[op]()
        if isinstance(n, ast.UnaryOp):
            v = ev(n.operand)
            return {ast.UAdd: lambda: +v, ast.USub: lambda: -v,
                    ast.Not: lambda: not v}[type(n.op)]()
        if isinstance(n, ast.BoolOp):
            vals = [ev(v) for v in n.values]
            return all(vals) if isinstance(n.op, ast.And) else any(vals)
        if isinstance(n, ast.Compare):
            left = ev(n.left)
            for op, comp in zip(n.ops, n.comparators):
                right = ev(comp)
                ok = {ast.Eq: left == right, ast.NotEq: left != right,
                      ast.Lt: left < right, ast.LtE: left <= right,
                      ast.Gt: left > right, ast.GtE: left >= right}[type(op)]
                if not ok:
                    return False
                left = right
            return True
        if isinstance(n, ast.IfExp):
            return ev(n.body) if ev(n.test) else ev(n.orelse)
        if isinstance(n, ast.Call):
            return _ALLOWED_FUNCS[n.func.id](*[ev(a) for a in n.args])
        raise ValueError(f"unhandled node in {expr!r}")     # unreachable

    return ev(tree)


def build_env(ctx):
    """The name space expressions see: stats, resource, combo, cast vars."""
    st = ctx.state
    env = dict(ctx.stats or {})
    env.update({"rp": st.rp if st else 0, "rp_max": st.rp_max if st else 0,
                "slot": ctx.slot,
                "combo": st.combo_index(ctx.slot) if st else 0})
    env.update(st.vars if st else {})
    env.update(ctx.vars)
    return env


def resolve_props(props, ctx):
    """Deep-resolve {"$": "expr"} values (in dicts and lists) against the env."""
    env = None

    def rez(v):
        nonlocal env
        if isinstance(v, dict):
            if set(v.keys()) == {"$"}:
                if env is None:
                    env = build_env(ctx)
                return safe_eval(v["$"], env)
            return {k: rez(x) for k, x in v.items()}
        if isinstance(v, list):
            return [rez(x) for x in v]
        return v

    return rez(props)


# --- rule executors -----------------------------------------------------------

RULES = {}


def rule(name):
    def deco(fn):
        RULES[name] = fn
        return fn
    return deco


def _amount(val, ctx):
    if isinstance(val, dict) and set(val.keys()) == {"$"}:
        return safe_eval(val["$"], build_env(ctx))
    if isinstance(val, str):
        return safe_eval(val, build_env(ctx))
    return val


@rule("Formula")
def _r_formula(ctx, r, out):
    ctx.vars[r["Var"]] = safe_eval(r["Expr"], build_env(ctx))


@rule("Branch")
def _r_branch(ctx, r, out):
    if "If" in r:
        chosen = r.get("Then") if safe_eval(r["If"], build_env(ctx)) else r.get("Else")
    elif r.get("On") == "aspect":
        active = ctx.state.active_aspect(list(r.get("Cases") or {}),
                                         now=ctx.source.now())
        chosen = (r.get("Cases") or {}).get(active, r.get("Default"))
    else:
        raise ValueError(f"Branch needs If or On:aspect, got {r}")
    if chosen:
        out.extend(run_rules(chosen, ctx))


@rule("ResourceOp")
def _r_resource_op(ctx, r, out):
    st = ctx.state
    op = r.get("Op")
    if op == "gain":
        st.gain_rp(_amount(r.get("Amount", 0), ctx))
    elif op == "spend":
        st.gain_rp(-_amount(r.get("Amount", 0), ctx))
    elif op == "spend_all":
        ctx.vars["spent"] = st.spend_all_rp()
    elif op == "set":
        st.rp = max(0, min(st.rp_max, int(_amount(r.get("Amount", 0), ctx))))
    else:
        raise ValueError(f"unknown ResourceOp {op!r}")


@rule("SetIndex")
def _r_set_index(ctx, r, out):
    ctx.state.set_combo_index(r.get("Slot", ctx.slot),
                              _amount(r.get("Index", 0), ctx))


@rule("SetAspect")
def _r_set_aspect(ctx, r, out):
    ctx.state.set_aspect(r["Aspect"], exclusive_group=r.get("Group"))


@rule("ApplyAura")
def _r_apply_aura(ctx, r, out):
    """Apply a registry aura server-side (mods/duration/stacking as data) and
    emit the client Aura node. Fires 'aura_applied' + the aura's own events."""
    name = r["Aura"]
    reg = AURA_REGISTRY.get(name) or {}
    targets = resolve_props(r, ctx).get("Targets")
    if targets is None:
        targets = [ctx.caster]
    now = ctx.source.now()
    for ts in targets:
        tstate = ctx.state if ts == ctx.caster else _target_state(ctx, ts)
        if reg.get("aspect"):
            tstate.set_aspect(name, exclusive_group=reg.get("group"))
        elif reg:
            tstate.apply_aura(name, reg.get("secs", 0) or float("inf"),
                              mods=reg.get("mods"),
                              max_stacks=reg.get("max_stacks", 1),
                              caster=ctx.caster, now=now)
    out.append(render_node(ctx, {
        "Name": "Aura", "AuraName": name,
        "Hide": r.get("Hide", bool(reg.get("hide"))),
        "Animation": r.get("Animation", ""),
        "Targets": targets,
        "uniquenessType": r.get("uniquenessType", reg.get("uniquenessType", 1)),
    }))
    fire_triggers(ctx, "aura_applied", extra={"aura": name})
    for ev in reg.get("events") or []:
        fire_triggers(ctx, ev, extra={"aura": name})


@rule("Trigger")
def _r_trigger(ctx, r, out):
    ctx.cast_triggers.setdefault(r["On"], []).append(r.get("Run") or [])


@rule("Emit")
def _r_emit(ctx, r, out):
    node = render_node(ctx, resolve_props(r["Node"], ctx))
    if node is not None:
        out.append(node)


def _target_state(ctx, ts):
    from .state import get_state
    return get_state(ts)


def fire_triggers(ctx, event, extra=None):
    """Run every rule sequence registered for `event` — the class config's
    triggers plus inline Trigger registrations from this cast. Nodes a trigger
    renders append to the cast's pending list (ctx.triggered_nodes)."""
    seqs = list((ctx.rules_config.get("triggers") or {}).get(event) or [])
    seqs = [seqs] if seqs and isinstance(seqs[0], dict) else seqs
    seqs += ctx.cast_triggers.get(event, [])
    if not seqs:
        return
    extra = extra or {}
    shadowed = {k: ctx.vars[k] for k in extra if k in ctx.vars}
    ctx.vars.update(extra)
    for seq in seqs:
        ctx.triggered_nodes.extend(run_rules(seq, ctx))
    for k in extra:
        ctx.vars.pop(k, None)
    ctx.vars.update(shadowed)


def run_rules(seq, ctx):
    """Execute one mixed sequence -> the render nodes it produced (in order)."""
    out = []
    for entry in seq:
        if not isinstance(entry, dict):
            continue
        if "Do" in entry:
            fn = RULES.get(entry["Do"])
            if fn is None:
                raise ValueError(f"unknown rule node {entry['Do']!r}")
            fn(ctx, entry, out)
        elif entry.get("Name") and entry["Name"] != "OnRequest":
            node = render_node(ctx, resolve_props(entry, ctx))
            if node is not None:
                out.append(node)
    return out


def run_skill(config, skill_id, ctx):
    """Execute a class rule config's sequence for one skill -> Attack Nodes
    (including anything triggers appended). The caller owns the envelope."""
    ctx.rules_config = config or {}
    seq = (ctx.rules_config.get("skills") or {}).get(str(skill_id)) or []
    nodes = run_rules(seq, ctx)
    if ctx.triggered_nodes:
        nodes.extend(ctx.triggered_nodes)
        ctx.triggered_nodes = []
    return nodes
