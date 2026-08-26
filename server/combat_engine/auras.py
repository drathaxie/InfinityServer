"""
The aura/buff registry — stat modifiers + durations + stacking rules as DATA.

Each entry is what the RULE layer needs to apply an aura server-side; the
client side is just the Aura render node (name + hidden flag). Modifier keys
are the vocabulary the damage/mitigation math reads (combat.py's live
ValueSource sums them via CombatState.modifier):

    dmg_taken_mult   incoming damage delta   (+0.20 = takes 20% more)
    dmg_dealt_mult   outgoing damage delta   (-0.10 = deals 10% less)
    crit_chance      additive crit chance    (-0.10 = 10 points less)
    next_hit_mult    bonus on the NEXT hit only (consumed by consume_on:"hit")
    hot_sp_frac      heal-per-tick as a fraction of the caster's spell power

Stacking rules: max_stacks caps, refresh extends the duration on re-apply,
consume_on drops the aura when its event fires. `events` are fired through
rules.fire_triggers when the aura lands (the six Infinity Hero branch buffs
fire "aspect_effect", which the class's Heroic-gain trigger listens for).
`aspect: True` entries are the hidden state MARKERS (they set CombatState
aspects, exclusive within `group`, rather than timed auras).

Grounding: names + which-skill-applies-which come from the captured class-2022
casts; Armor Melted's +20%/10s is the NODE_SPEC example; Suppression's -10%
crit/phys/magic 6s matches the Meteor tooltip already in seed.py. The other
magnitudes/durations are OURS (flagged) — AE never shipped the tooltips in any
capture we hold; they're tuned to read sensibly in-game and are trivially
editable here (or overridden via data/combat_auras.json, which deep-merges).
"""
import json
import pathlib

_OVERRIDE_FILE = pathlib.Path(__file__).resolve().parent.parent.parent \
    / "data" / "combat_auras.json"

_IH_ASPECTS = ["Warrior Aspect", "Mage Aspect", "Healer Aspect", "Rogue Aspect"]

AURA_REGISTRY = {
    # --- the four Infinity Hero aspect markers (hidden; 15s — the captured
    # --- IndexReset rings revert the branch icons after 15000ms) -------------
    "Warrior Aspect": {"aspect": True, "group": _IH_ASPECTS, "hide": True, "secs": 15},
    "Mage Aspect":    {"aspect": True, "group": _IH_ASPECTS, "hide": True, "secs": 15},
    "Healer Aspect":  {"aspect": True, "group": _IH_ASPECTS, "hide": True, "secs": 15},
    "Rogue Aspect":   {"aspect": True, "group": _IH_ASPECTS, "hide": True, "secs": 15},

    # --- the six Infinity Hero branch buffs (each is an "Aspect Effect") -----
    # Armor Melted: +20% damage taken for 10s (NODE_SPEC's example numbers).
    "Armor Melted": {"secs": 10, "mods": {"dmg_taken_mult": 0.20},
                     "max_stacks": 1, "refresh": True,
                     "events": ["aspect_effect"]},
    # Prepared Strike: your next strike hits 30% harder (magnitude OURS).
    "Prepared Strike": {"secs": 8, "mods": {"next_hit_mult": 0.30},
                        "max_stacks": 1, "refresh": True, "consume_on": "hit",
                        "events": ["aspect_effect"]},
    # Suppression: -10% Crit Chance / Physical / Magical for 6s (the Meteor
    # tooltip's numbers, already live in combat.py's AURA_FX). uniquenessType 0
    # on the wire, per the captured class-2022 casts.
    "Suppression": {"secs": 6, "mods": {"crit_chance": -0.10,
                                        "dmg_dealt_mult": -0.10},
                    "max_stacks": 1, "refresh": True, "uniquenessType": 0,
                    "events": ["aspect_effect"]},
    # Holy Guard: -20% incoming damage for 6s (magnitude OURS, mirrors the
    # Paladin's Guard band).
    "Holy Guard": {"secs": 6, "mods": {"dmg_taken_mult": -0.20},
                   "max_stacks": 1, "refresh": True,
                   "events": ["aspect_effect"]},
    # Hallowed Footsteps: a 20%-of-SP heal each second for 6s (numbers OURS;
    # uniquenessType 0 on the wire, per capture).
    "Hallowed Footsteps": {"secs": 6, "mods": {"hot_sp_frac": 0.20},
                           "tick_secs": 1.0, "max_stacks": 1, "refresh": True,
                           "uniquenessType": 0, "events": ["aspect_effect"]},
    # Concealed Blade: next strike +50% and +25% crit chance (numbers OURS).
    "Concealed Blade": {"secs": 8, "mods": {"next_hit_mult": 0.50,
                                            "crit_chance": 0.25},
                        "max_stacks": 1, "refresh": True, "consume_on": "hit",
                        "events": ["aspect_effect"]},

    # --- Heroic Empowerment: the 25-stack empowered Heroic Strike (the 2.5s
    # --- sky-blade AoE window; the damage payoff rides the skill's own
    # --- Branch, this aura is the visible marker) ----------------------------
    "Heroic Empowerment": {"secs": 2.5, "mods": {}, "max_stacks": 1,
                           "refresh": True, "hide": True},

    # Chronomancer control effects. The combat host owns the actual pacing;
    # these two extra fields are copied onto the ordinary Aura render node so
    # InfinityLoader can mirror it by slowing only the affected monster's
    # Animators. They do not add a new Skill Forge node type.
    "Time Dilation": {"secs": 6.0, "mods": {}, "max_stacks": 1,
                      "refresh": True, "animation_speed": 0.35},
    "Temporal Stasis": {"secs": 2.5, "mods": {}, "max_stacks": 1,
                        "refresh": True, "animation_speed": 0.0},
}


def _deep_merge(base, over):
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _deep_merge(base[k], v)
        else:
            base[k] = v


def load_overrides():
    """Merge data/combat_auras.json over the built-in registry (live tuning
    without a code change). Missing/invalid file is a no-op."""
    try:
        over = json.loads(_OVERRIDE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return
    if isinstance(over, dict):
        _deep_merge(AURA_REGISTRY, over)


load_overrides()
