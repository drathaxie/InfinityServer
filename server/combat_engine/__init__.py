"""
combat_engine — the data-driven combat node engine.

Two layers (docs/combat-engine/NODE_SPEC.md):

  1. RENDER layer: the `Nodes` array an Attack packet carries. Each node type
     mirrors one decompiled AE `Node*` executor (docs/combat-engine/
     ae_node_semantics.cs) — same Name, same prop schema, same defaults. The
     renderers live in nodes.py, keyed by Name in a registry, and are verified
     by replaying the 549 captured AE Attack packets (test_combat_engine.py).
  2. RULE layer: server-side data nodes (Formula/Condition/Trigger/ResourceOp/
     SetIndex) that decide the NUMBERS the render nodes carry, executed against
     a per-caster CombatState (state.py). This is what replaces the per-class
     Python in combat.py: a new class/boss is authored data, not code.

The engine is deliberately decoupled from combat.py: dynamic values (damage
rolls, HP mutation, clocks) flow through a ValueSource (engine.py) so the same
graph renders in production (live rolls) and in replay tests (captured
outcomes) with identical structure.
"""
from .state import CombatState, get_state, drop_state, all_states          # noqa: F401
from .engine import (ValueSource, ReplayValueSource, RenderContext,        # noqa: F401
                     render_graph, build_attack, walk_graph)
from .nodes import RENDERERS, render_node                                  # noqa: F401
from .rules import safe_eval, run_rules, run_skill, fire_triggers          # noqa: F401
from .auras import AURA_REGISTRY                                           # noqa: F401
