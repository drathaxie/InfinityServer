# Data-Driven Combat Node Engine — Build Spec

Goal: make combat mechanics **data**, not per-class Python. Today skill graphs are
data (SkillForge-editable) but every stateful class mechanic (Paladin Conviction,
the Infinity Hero Aspect/Heroic system, boss tile-attacks) is hardcoded in
`server/combat.py`, keyed by `skill_id`. Adding a class means writing Python. This
project builds an engine expressive enough that a new class/boss is **pure data**,
reconstructable straight from a packet capture.

This is grounded in AE's OWN system — do not invent a vocabulary. The target is to
mirror AE's real `Node*` classes and validate against AE's real Attack packets.

## The two layers (read this first — it's the whole architecture)

A captured `Attack` packet is AE's **server output**: a list of render `Nodes` the
client executes. `NodeResource.Execute` just does `caster.RP = props["Amount"]`;
`NodeSetSkillIndex` just swaps the slot icon `props["Icon"]` the server chose. So:

1. **Render layer (client-facing nodes)** — the `Nodes` array we emit in `Attack`.
   Fully data. Must match AE's 49 `Node*` types exactly (see `ae_node_semantics.cs`).
   This is *what the client draws*.
2. **Rule/state layer (server)** — decides the numbers in those nodes: damage from
   stats, "+1 Heroic per Aspect Effect," "which combo icon," aura stacking. On AE
   this is server logic; a captured packet is a *snapshot for one state*, NOT the
   rule. To make THIS data-driven we build a small server-side rule graph
   (formula / condition / trigger / resource nodes) + a per-caster state context.

Both must be data-authored for a class to need zero Python.

## What exists today (extend, don't rewrite)

- `server/forge.py` — `linear_graph(nodes)` builds a linear node chain; SkillForge
  CRUD (`sf_new/edit/save`). Node vocabulary is a handful (Damage, Aura, Cooldown,
  Particle, Range…).
- `server/combat.py` — `begin_cast`/`cast_skill` walk the graph and emit `Attack`
  nodes, PLUS Python special-cases keyed by `skill_id` (Conviction, the mislabeled
  `INFINITY_*` Paladin aspect code, empower, Smite lifelink). `_active_aspect` dict
  is per-uid runtime state — the seed of a state context.
- DB: `skills(skill_id, data, forge, …)`, `classes(class_id, name, rig, resource)`.
  `classes.resource` is a data blob `{"model":"conviction","MaxRP":50,…}` whose
  `model` STRING dispatches to Python — exactly the pattern to replace with data.

## Target

1. **Complete node interpreter** — implement all 49 `Node*` types from
   `ae_node_semantics.cs`. Prioritize by real usage (histogram below).
2. **Per-caster combat state context** — resources (RP), aspects/tags, combo slot
   index, active auras/buffs with durations + stat modifiers. Replaces the
   `_active_aspect` dict and the `resource "model"` Python dispatch.
3. **Server rule graph** — data node types for the *rules*: `Formula`
   (damage=f(STR,INT,DEX,WIS)), `Condition`/`Branch` (on aspect/threshold),
   `Trigger` (on aura-applied → resource +N), `ResourceOp` (gain/spend/cap),
   `SetIndex` (combo). Authored in the skill/class data; the interpreter executes it.
4. **Aura/buff registry as data** — `Armor Melted (+20% dmg taken, 10s)`, `Holy
   Guard`, `Suppression`, etc. — modifiers + durations as data, not `combat.py` dicts.

## Node vocabulary — priority order (by real usage in the 549 captures)

Implement in this order; each must reproduce its golden-master output.

```
SetSkillIndex(350) Cooldown(263) Damage(257) SoundFX(197) IndexReset(175)
PlayerAnimation(171) Range(145) Particle(134) Aura(127) UpdateAnimation(115)
Restrict(87) Interruptable(87) InstantDamage(53) Resource(50) AnimationHitbox(44)
DispenseDamage(44) ImpactSoundFX(43) SpellAnimation(43) RangeMulti(15)
PlayerHitStream(8) DashToTarget(4)
```
Then the remaining declared types (tile-attacks for bosses: `HitTiles`,
`TileWave`, `TileCluster`, `TileMove`, `TileSafe`, `TileTrack`; plus `Channel`,
`SwapSkill`, `MonTransform`, `SpawnPickup`, etc.) — full list + prop schemas +
decompiled `Execute`/`Input` bodies in **`ae_node_semantics.cs`** (49 types).

## Golden-master fixtures (this is how you verify — no live client needed)

`fixtures/golden_attack_fixtures.json` — 549 real AE `Attack` packets:
`{caster, kind, classID, slot, statusCode, nodes:[…]}`.
- `fixtures/infinity_hero_casts.json` — 233 casts, **class 2022 (Infinity Hero)**.
- `fixtures/monster_casts.json` — 102 monster casts (incl. **Abomilich**, mon 429).

**Acceptance = replay match.** Given a skill graph + caster state, the engine must
emit a `Nodes` array matching AE's for that cast (structural match on node
Name/props; numeric fields like `Damages` validated against the state that produced
them, since damage scales with stats).

## Validation targets (prove the engine)

1. **Port Paladin (69420) + Void (2064) to pure data** — re-express `_PALADIN_SKILLS`
   / `_VOID_SKILLS` and their Conviction/Hunger mechanics as data graphs. Existing
   behavior must be unchanged (regression tests).
2. **Build Infinity Hero (class 2022) as pure data** — the real spec:
   - Skills 168 Heroic Strike, 169 Definitive Strike, 170 Meteor, 171 Healing Oath,
     172 Serpent's Kiss.
   - **4 Aspects**: Warrior(169) Mage(170) Healer(171) Rogue(172); each skill's
     bonus branches on the *active* aspect (see the decoded matrix in the spec issue
     / skill descriptions in the fixtures).
   - **Heroic resource** 0–50; +1 per Aspect Effect applied; at 25 → next Heroic
     Strike = Heroic Empowerment (sky-blade AoE over 2.5s).
   - Combo via `SetSkillIndex`/`IndexReset` (350+175 uses in the fixtures show the
     exact rebind sequence).
   - 6 buffs: Armor Melted, Prepared Strike, Suppression, Holy Guard, Hallowed
     Footsteps, Concealed Blade.
   - Class row (rig + resource block), granting armor item, `seed_infinity_hero`.
   Must replay-match the 233 class-2022 casts.
3. **Abomilich boss** — reconstruct its telegraphed tile-attack set from
   `monster_casts.json` using the `Tile*`/`HitStream` nodes.

## Constraints

- Additive + non-destructive: SkillForge edits and existing classes must not break.
  Insert-if-absent seeding, graph-version bumps like `PALADIN_GRAPH_VERSION`.
- Python 3.10, stdlib only (matches the server). Dialect-neutral SQL via `db.py`
  (`?` placeholders; it translates for Postgres/SQLite).
- Everything replay-tested; no reliance on a live game client.
- Prod is authoritative and live — land in git, deploy is separate.
