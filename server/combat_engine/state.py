"""
Per-caster combat state — the server-side context every cast renders against.

Replaces the loose per-uid dicts scattered through combat.py (`_active_aspect`,
`_rp`, `_conv_cast_stacks`, ...) with one queryable object:

  - resource points (rp) + cap, the class's stacking/mana pool
  - aspects/tags: named markers with optional expiry ("Warrior Aspect" active)
  - combo slot indices: which SetSkillIndex rebind step each slot sits at
  - active auras: name -> stacks/expiry/stat-modifiers (data-driven, stage 3)

State is keyed by the caster's target string ("p:<uid>" / "m:<mapid>") so
players and monsters share the machinery. Time is injected (now) so tests are
deterministic; production passes time.time().
"""
import time


class Aura:
    """One active aura on this caster: expiry + stacks + the stat modifiers it
    grants (modifier keys are data-defined in the aura registry, stage 3)."""

    __slots__ = ("name", "ends", "stacks", "mods", "caster")

    def __init__(self, name, ends, stacks=1, mods=None, caster=""):
        self.name = name
        self.ends = ends
        self.stacks = stacks
        self.mods = dict(mods or {})
        self.caster = caster

    def active(self, now=None):
        return (now if now is not None else time.time()) < self.ends


class CombatState:
    """Everything the rule layer may read/write about one caster."""

    def __init__(self, caster_ts, rp_max=100):
        self.caster_ts = caster_ts
        self.rp = 0
        self.rp_max = rp_max
        self.aspects = {}           # name -> expiry ts (float('inf') = until replaced)
        self.combo = {}             # slot -> current rebind index (SetSkillIndex step)
        self.auras = {}             # name -> Aura
        self.vars = {}              # scratch counters the rule layer defines (data-named)

    # --- resource ------------------------------------------------------------
    def gain_rp(self, n):
        self.rp = max(0, min(self.rp_max, self.rp + int(n)))
        return self.rp

    def spend_all_rp(self):
        spent, self.rp = self.rp, 0
        return spent

    # --- aspects (named tags, e.g. the InfinityHero's active Aspect) ---------
    def set_aspect(self, name, ends=float("inf"), exclusive_group=None):
        """Activate an aspect. exclusive_group = iterable of names that can't
        coexist with it (the 4 InfinityHero aspects replace each other)."""
        if exclusive_group:
            for other in exclusive_group:
                if other != name:
                    self.aspects.pop(other, None)
        self.aspects[name] = ends

    def aspect_active(self, name, now=None):
        ends = self.aspects.get(name)
        if ends is None:
            return False
        if (now if now is not None else time.time()) >= ends:
            del self.aspects[name]
            return False
        return True

    def active_aspect(self, candidates, now=None):
        """The first of `candidates` currently active (None if none are)."""
        for name in candidates:
            if self.aspect_active(name, now):
                return name
        return None

    # --- auras ---------------------------------------------------------------
    def apply_aura(self, name, secs, stacks=1, mods=None, caster="",
                   max_stacks=None, now=None):
        now = now if now is not None else time.time()
        cur = self.auras.get(name)
        if cur is not None and cur.active(now):
            cur.ends = now + secs               # refresh duration
            cur.stacks = min(cur.stacks + stacks,
                             max_stacks if max_stacks else cur.stacks + stacks)
            cur.mods = dict(mods or cur.mods)
            return cur
        aura = Aura(name, now + secs, stacks, mods, caster)
        self.auras[name] = aura
        return aura

    def aura(self, name, now=None):
        """The active Aura object by name, or None (expired auras are dropped)."""
        a = self.auras.get(name)
        if a is None:
            return None
        if not a.active(now if now is not None else time.time()):
            del self.auras[name]
            return None
        return a

    def drop_aura(self, name):
        return self.auras.pop(name, None)

    def expired_auras(self, now=None):
        """Pop and return every expired aura (for expiry broadcasts)."""
        now = now if now is not None else time.time()
        out = [a for a in self.auras.values() if not a.active(now)]
        for a in out:
            del self.auras[a.name]
        return out

    def modifier(self, key, now=None):
        """Sum of one stat modifier across active auras (e.g. 'dmg_taken_mult'
        contributions are ADDITIVE deltas the rule layer combines)."""
        now = now if now is not None else time.time()
        return sum(a.mods.get(key, 0.0) * (a.stacks if a.mods.get("per_stack") else 1)
                   for a in self.auras.values() if a.active(now))

    # --- combo ---------------------------------------------------------------
    def combo_index(self, slot):
        return self.combo.get(int(slot), 0)

    def set_combo_index(self, slot, index):
        self.combo[int(slot)] = int(index)

    def reset_combo(self):
        self.combo.clear()


_states = {}                # caster_ts -> CombatState


def get_state(caster_ts, rp_max=100):
    st = _states.get(caster_ts)
    if st is None:
        st = _states[caster_ts] = CombatState(caster_ts, rp_max)
    return st


def drop_state(caster_ts):
    _states.pop(caster_ts, None)


def all_states():
    return dict(_states)
