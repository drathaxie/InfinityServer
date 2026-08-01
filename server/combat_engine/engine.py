"""
The interpreter: walk a skill graph against a CombatState, emit Attack Nodes.

Dynamic values are injected through a ValueSource so the SAME renderers serve
two callers:

  - production: a live ValueSource rolls damage / mutates HP / stamps clocks
    (combat.py wires one up around its _hit/_mon machinery);
  - replay tests: ReplayValueSource echoes the outcome a captured packet
    recorded, so the harness can assert the renderer rebuilds the packet
    byte-for-byte (structure + defaults are the engine's; numbers are pinned).

Graphs come in the SkillForge shape ([data, forge] — see forge.linear_graph)
or as a plain ordered props list (what deauthoring a capture yields).
"""
import time

from .nodes import render_node
from .state import get_state


def walk_graph(data, forge):
    """Ordered [(nodeId, props), ...] by following the header's Next chain in
    the ForgeData tree (same walk as combat.py). Tolerant of short graphs."""
    nodes = data[1] if isinstance(data, list) and len(data) > 1 and isinstance(data[1], dict) else {}
    tree = forge[1] if isinstance(forge, list) and len(forge) > 1 and isinstance(forge[1], dict) else {}
    order = []
    seen = set()
    for _hid, hnode in tree.items():
        nxt = hnode.get("Next") if isinstance(hnode, dict) else None
        while isinstance(nxt, dict) and nxt.get("id") and nxt["id"] not in seen:
            nid = nxt["id"]
            seen.add(nid)
            order.append((nid, nodes.get(nid, {}) or {}))
            nxt = nxt.get("Next")
        break
    return order, nodes


class ValueSource:
    """Where the interpreter gets server-computed values. Subclass per caller."""

    def damage(self, ctx, props):
        """-> (DamageTypes, Damages, Targets, TargetHPs) or None to skip the
        node (e.g. nothing living to hit)."""
        raise NotImplementedError

    def resource_total(self, ctx, props):
        """The absolute RP the Resource node reports (post-cast pool)."""
        st = ctx.state
        return st.rp if st is not None else int(props.get("Amount") or 0)

    def timestamp_ms(self):
        return int(time.time() * 1000)

    def now(self):
        return time.time()


class ReplayValueSource(ValueSource):
    """Echo the captured outcome: authored props carry the recorded numbers."""

    def damage(self, ctx, props):
        return (list(props.get("DamageTypes") or []),
                list(props.get("Damages") or []),
                list(props.get("Targets") or []),
                list(props.get("TargetHPs") or []))

    def resource_total(self, ctx, props):
        return props.get("Amount", 0)


# helper-node names a Targets ref may point at (SkillForge authoring)
_SELF = {"self"}
_TARGET = {"target"}
_ALLIES = {"allies", "allallies", "party", "allinrangeallies"}
_ENEMIES = {"allenemies", "allinrange", "area"}


class RenderContext:
    """Everything one cast's renderers may read: who casts, at what, the graph's
    helper nodes, the caster's CombatState, and the ValueSource for computed
    values. `allies`/`enemies` are resolver callables the host supplies (the
    server knows the area; replay pins explicit lists)."""

    def __init__(self, caster, slot=0, target=None, targets=None, nodes=None,
                 state=None, source=None, allies=None, enemies=None, stats=None):
        self.caster = caster
        self.slot = slot
        self.target = target
        self.targets = list(targets or ([target] if target else []))
        self.nodes = nodes or {}
        self.state = state if state is not None else get_state(caster)
        self.source = source or ReplayValueSource()
        self._allies = allies
        self._enemies = enemies
        self.stats = dict(stats or {})      # STR/INT/DEX/WIS/... for Formula
        self.vars = {}                      # per-cast scratch (Formula vars, spent)
        self.rules_config = {}              # the class rule config (rules.run_skill)
        self.cast_triggers = {}             # inline Trigger registrations this cast
        self.triggered_nodes = []           # render nodes trigger sequences emitted

    def resolve_targets(self, props, default=None):
        """Resolve a node's Targets: an explicit list passes through (replay /
        pre-resolved); a {id} ref dispatches on the helper node's Name
        (Self/Target/Allies/AllEnemies); an "@keyword" string is the rule
        layer's direct form (@self/@target/@allies/@enemies/@hits — @hits is
        the monsters this cast's Damage nodes actually struck); absent -> the
        cast's default set."""
        t = props.get("Targets")
        if isinstance(t, list):
            return list(t)
        name = None
        if isinstance(t, str) and t.startswith("@"):
            name = t[1:].lower()
        elif isinstance(t, dict) and t.get("id") is not None:
            name = (self.nodes.get(str(t["id"]), {}) or {}).get("Name", "").lower()
        if name is not None:
            if name in _SELF:
                return [self.caster]
            if name in _TARGET:
                return list(self.targets) or [self.caster]
            if name in _ALLIES:
                return self._ally_list()
            if name in _ENEMIES:
                return self._enemy_list() or list(self.targets)
            if name == "hits":
                return list(self.vars.get("_hits") or [])
        if default is not None:
            return list(default)
        return list(self.targets) or [self.caster]

    def _ally_list(self):
        out = [self.caster]
        for a in (self._allies() if callable(self._allies) else (self._allies or [])):
            if a and a != self.caster and a not in out:
                out.append(a)
        return out

    def _enemy_list(self):
        return list(self._enemies() if callable(self._enemies) else (self._enemies or []))


def render_graph(order, ctx):
    """Render an ordered node sequence -> the Attack Nodes list. `order` is
    either [(id, props), ...] (a graph walk) or [props, ...] (deauthored).
    Unknown node types are skipped, same as combat.py's tolerant walk."""
    out = []
    for entry in order:
        props = entry[1] if isinstance(entry, tuple) else entry
        if not isinstance(props, dict) or props.get("Name") in (None, "OnRequest"):
            continue
        node = render_node(ctx, props)
        if node is None:
            continue
        if isinstance(node, list):
            out.extend(node)
        else:
            out.append(node)
    return out


NS_SUCCESS, NS_PENDING = 1, 2


def build_attack(caster, slot, nodes, status=NS_SUCCESS, wait=True):
    """The Attack envelope (same shape combat.py emits)."""
    return {"Cmd": "Attack", "Caster": caster, "Slot": slot, "StatusCode": status,
            "Wait": wait, "Error": "", "Nodes": nodes}
