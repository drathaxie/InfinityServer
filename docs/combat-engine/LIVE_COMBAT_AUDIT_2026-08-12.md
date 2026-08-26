# Live combat audit and runtime update — 2026-08-12

## Outcome

The data-driven renderer was not the source of the rough live experience. It
continues to replay 884 captured casts / 3,837 nodes exactly. The defects were
at the runtime boundary around it: cast admission, the server-sustained auto
loop, resource ownership, monster action packets, and combat cleanup.

This update makes manual casts and server-driven repeat actions use one combat
contract while preserving the existing wire protocol, SkillForge graphs,
class-rule data, and legacy parity fallback.

## Findings and corrections

### Cast admission

- Class slots 0–4 previously executed without a living target. They now require
  a registered living monster or reuse the player's active engagement target.
- Slot 5 remains available outside combat for spellstones, transformations, and
  other self-directed item skills.
- Rejected casts now send terminal `Attack/Fail` packets. Silently ignoring a
  request could leave the client's skill execution state waiting indefinitely.
- Dead players cannot cast.
- Cooldown admission happens after target and mana validation. Explicitly
  cancelled range handshakes release the cooldown and spend no resource.
- Rest is rejected while a live combat engagement exists.

### Sustained auto-attacks

- The old loop discarded `skill_id`, allies, and class-rule context after the
  first manual auto, then called the legacy graph walker directly.
- `_auto` now retains the authored skill ID, and both manual and repeated autos
  call `execute_auto()`. Data-driven classes therefore keep their rule graph,
  combo behavior, and resource operations on every swing.
- Every landed repeat auto refreshes the monster's aggro lease; a fight no
  longer expires merely because the player relies on the server auto loop.
- A failed pure-data repeat stops auto-attacking instead of broadcasting errors
  or repeatedly retrying an incompatible path.

### Resources and mana

- Mana affordability is validated before cooldown or execution. An
  insufficient cast returns the client-compatible error
  `Not enough Mana!,<required>` and does not floor the pool to zero.
- Any authoritative resource change now sends an `hpmp` resync: mana,
  Determination, Conviction, Heroic, and generic stacking pools.
- Input-node continuations (`gai`) also resync after their deferred resource
  mutation.
- Mana rest now fills the equipped class's actual `MaxRP`, not the global 100
  fallback.
- Slot 5 / external casts no longer build or spend the equipped class resource.
- Generic `stacking` is included in the stack-resource model family.
- Pure-data Heroic/stacking classes fail closed if their rule configuration
  breaks. Falling back to legacy Determination would corrupt their resource
  semantics. Paladin/Void keep the existing parity-tested Python fallback.
- Five captured tooltips declared costs that were absent from mined `regMana`.
  A guarded one-time seed correction now applies: Prepared Strike 10, On Guard
  15, Healing Word 20, Stiletto 15, and Footwork 5.

### Monster behavior and presentation

- Basic monster attacks previously emitted only `Damage`, which changed HP
  without asking the client to animate the monster.
- They now match all 102 captured monster auto packets:
  `Damage -> PlayerAnimation -> Cooldown`, using
  `Attack1,Attack2,Attack3`, `Priority: Low`, and 2,250 ms cooldown.
- Server melee cadence now matches that captured 2,250 ms cooldown.
- A monster special/tile/summon cast consumes that tick's action. It no longer
  lands a simultaneous basic swing underneath its telegraph.
- Existing Chronomancer slow/stasis multipliers continue to scale the corrected
  base cadence.

### Lifecycle cleanup

- Death, revive, cell transfer, map transfer, disconnect, and class swap cancel
  sustained autos, aggro, pending input handshakes, in-flight class state, and
  delayed packets owned by the player.
- Class swaps also clear old slot cooldowns. An old class auto or meteor field
  can no longer execute against the newly equipped class/resource model.
- Disconnect cleanup now drops the data-engine `CombatState` and class rules in
  addition to the legacy dictionaries.

## Verification

- `python server/test_combat_runtime.py`
  - admission and terminal failures
  - mana affordability and exact spend
  - rest gating
  - data-rule sustained autos and aggro refresh
  - monster animation packet shape
  - pure-data failure behavior
  - class-cap rest
  - spellstone resource isolation
  - lifecycle cleanup
  - cancelled input cast rollback
- `python server/test_combat.py` — full legacy combat harness passes.
- `python server/test_combat_engine.py` — 884 casts / 3,837 nodes, zero mismatches.
- `python server/test_port_parity.py` — Paladin/Void 16/16 parity passes.
- `python server/test_cutover.py` — Infinity Hero live cutover passes.
- `python server/test_chronomancer.py` and
  `python server/test_chronomancer_red_dragon.py` pass.
- `python server/test_spellstone.py` passes.
- `python server/test_forge.py` and `python server/test_seed.py` pass, including
  the one-time mana-cost correction and non-clobbering reseed.
- Pytest-compatible focused selection: 14 passed.
- Project aggregate runner `pytest server/test_all.py`: 33 passed.

## Remaining work and honest limits

- Several base-class mechanics described by tooltips are still simplified or
  absent (for example Prepared Strike consumption, On Guard/Arcane Shield,
  Energy Flow interactions, and Rogue avoidance/Viper mechanics). Correct cast
  admission and mana costs make the loop coherent, but do not implement every
  tooltip mechanic.
- Monster special attacks are still driven by the existing mixed architecture:
  SkillForge supplies tile/summon data while `server.py` owns scheduling. A
  future refactor should move scheduling into a testable `CombatWorld` service,
  but that is not required to correct the live defects above.
- Server distance validation remains client-assisted through Range/Hitbox input
  nodes. The server now validates target existence/liveness/area, but it does
  not maintain authoritative coordinates for every actor.
- The focused automated suite cannot replace an in-client feel pass. Validate
  one mana class, one Determination class, one pure-data stacking class, and a
  tile-skill boss before production deployment.
