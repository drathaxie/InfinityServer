# Combat node engine — delivery report

A data-driven combat engine for AQWorlds-Infinity. Class mechanics stop being
per-class Python and become authored data: node graphs for what the client
renders, rule graphs for what the server decides.

Everything below is measured by the test suite in `server/`, not estimated.

---

## 1. What shipped

| | |
|---|---|
| Render node types | **49 / 49** AE `Node*` types |
| Rule node types | 12 (`Formula` `Branch` `ResourceOp` `SetIndex` `SetAspect` `SetVar` `ApplyAura` `Heal` `Trigger` `Emit` `Graph` `Packet`) |
| Aura registry | 11 entries (6 branch buffs, 4 aspect markers, 1 empowerment) |
| Engine source | 1 599 lines across 7 modules |
| Tests | 1 227 lines across 4 harnesses |

```
server/combat_engine/
  nodes.py    579   the 49 renderers, mirrored from AE's decompiled Node*.Execute
  rules.py    407   the rule layer + a whitelisted-AST expression evaluator
  engine.py   180   graph walk, RenderContext, ValueSource, Attack envelope
  state.py    152   per-caster CombatState: pool, aspects, auras, combo indices
  live.py     151   bridge onto combat.py's real rolls/HP/aura machinery
  auras.py    104   buff registry as data (+ data/combat_auras.json override)
```

### The two layers

**Render nodes** are client-facing and mirror AE exactly: required keys always
present with AE's defaults, optional keys only when authored — because that is
what the captured packets do. **Rule nodes** never reach the client; they run
against `CombatState` and decide the numbers the render nodes carry.

Values that only the server can know (damage rolls, remaining HP, timestamps,
cooldowns) come through a `ValueSource`, so one renderer serves both a live
cast and a replayed capture. That indirection is what makes the golden-master
testing below possible at all.

---

## 2. Match rates

### Render layer — 884 captured AE casts, exact

```
golden_attack_fixtures.json   549 casts   2412/2412 nodes   0 mismatches
infinity_hero_casts.json      233 casts   1119/1119 nodes   0 mismatches
monster_casts.json            102 casts    306/306  nodes   0 mismatches
                              ---------   -----------------
                              884 casts   3837 nodes        byte-for-byte
```

Each cast is replayed **twice**: once verbatim, and once with every default and
computed key stripped, so the refill has to reproduce AE's defaults from the
engine rather than echo the fixture. `server/test_combat_engine.py`.

### Paladin + Void port — 16/16 casts identical to the Python they replace

Conviction and Hunger re-expressed as rule configs, then diffed against the
live `combat.py` path cast-for-cast with pinned RNG: every Attack packet, pool
value, kill list and damage total matches. Coverage spans builders at several
stack levels, both Meteor aspect branches, the spenders full and empty, and
party lifelink/guard. Report: `port_parity_report.md`.

Harness sensitivity was mutation-checked (a changed multiplier, a dropped
lifelink, a wrong build amount each produce failures).

**The Python path is still what runs.** The data path is proven equivalent and
sits alongside it; the cutover is a separate, revertible step.

### Infinity Hero (class 2022) — 155/156 graded presses, from data alone

Two real AE sessions, 447 packets → 156 graded presses, 2 051 nodes compared.

| session | presses | exact | note |
|---|---|---|---|
| `infinity_hero_casts.json` | 86 | **85** | 1 known AE variance |
| `golden_attack_fixtures.json` (2nd caster) | 74 | **70** | 4 casts AE interrupted mid-animation |

Reproduced purely from `seed.INFINITY_HERO_RULES`: which of the four Aspects is
active, which branch each press takes, the effect aura it applies and to whom,
the Heroic pool arithmetic, the arm-at-25 ultimate, the per-aspect combo-rebind
broadcast, and every static prop (sounds, particles, hitboxes, ranges, icons,
timings).

Seven deliberate breakages of the config are asserted to fail the replay, so
the match is not a lax comparison. Report: `infinity_hero_report.md`.

### Abomilich — the boss AE never finished

Seven skills across the whole tile vocabulary, wired onto InfinityLichBoss
(mon 429/430) through the existing monster-class mechanism, editable in the
SkillForge. Verified: every node is a real AE type rendering the captured prop
set; all six tile mechanics used; the fight round-trips through the DB into a
rotation the AI can cast (summon included, adds capped); the boss's plain auto
replays exactly against all 102 captured monster casts.

---

## 3. What was decoded from the captures

The Infinity Hero mechanics were not documented anywhere — they were recovered
from 447 packets and then confirmed by the replay:

- **The Aspect combo.** Each skill applies its own hidden Aspect and branches on
  whichever was already active. Confirmed twice over: by which effect aura
  appears per prior Aspect, and independently by the icon rebinds — after an
  Aspect lands, exactly its two branch skills swap to that family's icons with
  15 s shared `IndexReset` rings back to base.
- **"+1 Heroic per Aspect Effect" means per BRANCH, not per aura.** The
  Warrior-branch Meteor grants Heroic while applying no aura at all, and the
  Mage-branch Serpent's Kiss does the same. Counting auras would have been
  wrong in exactly those two places, and the pool would have drifted.
- **Arming at 25** emits the empowerment aura on the caster, then re-emits it
  with an *empty* target list on every later press while still armed — plus an
  `UpdateAnimation` that those skills otherwise never send.
- **A dead target erases its debuff.** When everything struck dies, AE drops the
  aura node entirely rather than sending it empty.
- **The gap-closer skips the resource gain.** A Serpent's Kiss that leads with a
  dash grants no Heroic and skips `Restrict` — an AE quirk, kept.
- **Cooldowns drift with gear.** The same skill is 3959 ms in one session and
  3918 ms in the other, so CD is a server-computed value, not an authored one.

---

## 4. Unresolved gaps

Stated plainly, with what would close each.

1. **The Python path still drives live combat.** Stage 4 proves the data path
   equivalent; it does not switch to it. Cutover = route `combat.cast_skill`
   through `combat_engine.live.cast_skill_data` when the class has a rule
   config, keeping the parity test as the gate.

2. **Aura magnitudes are ours, not AE's.** Armor Melted (+20 %/10 s) and
   Suppression (−10 %/6 s) come from AE's own tooltips. Prepared Strike, Holy
   Guard, Hallowed Footsteps and Concealed Blade have no captured tooltip — the
   numbers in `auras.py` are ours and flagged there. Tunable live via
   `data/combat_auras.json` without a code change.

3. **Damage multipliers are ours.** No capture reveals AE's server formula. The
   ratios are tuned to the captured damage bands. This is why the Infinity Hero
   test feeds damage numbers from the capture and grades everything else.

4. **One AE self-contradiction.** A single Serpent's Kiss shipped its Concealed
   Blade with an empty target list, landing the buff on nobody; the other seven
   target the caster. We follow the seven, and the one is pinned as a named
   known-variance so a real regression can't hide under it.

5. **The async meteor field is graded by shape, not bytes.** It is sent about a
   second after the cast and its targets resolve against the world *then* —
   which monsters are still standing at that moment isn't in the capture.
   Node order and every static prop are still compared. This is the only place
   byte-exactness is not asserted.

6. **Abomilich's rotation is authored, not captured — and cannot be captured
   from this source.** AE's telegraphed tiles are client-rendered and reported
   back over `MonReq`/`gmah`, so they never travel as monster Attack packets;
   the 102 monster casts in the fixtures are plain autos containing no tile
   node at all. The node *grammar* is capture-verified and the cadence is tuned
   against Ragnafluff's captured 4.5/5/7/18 s. Closing this needs a live packet
   capture of the fight, which does not exist because AE never shipped it.

7. **Mon 429's art was dead.** Its bundle 78660 404s on the live CDN at every
   version while its twin 78661 serves at v1–v3, so the boss rendered as an
   invisible hitbox. Repointed with a guarded update that won't clobber a later
   real upload. Worth re-checking if AE ever publishes 78660.

8. **Class 2022 was never itemized by AE.** No catalog item carries
   `MetaString 2022`, so the class armor is ours — item 200022 in our homebrew
   band, wearing the Kickstarter "Hero of Infinity" bundle (77678) with the
   unshipped `classInfinityHero` particle bundle (78541).

9. **Interrupted casts are unverified.** Four captured activations were aborted
   mid-animation and have no resolution packet, so nothing can be compared for
   them; they are counted and reported separately, never silently as passes.

---

## 5. Deploying

Nothing here has been deployed — this is git only, as the brief required.
Changes are additive and non-clobbering: every seed is insert-if-absent, and
graph rewrites are gated behind version bumps, so live SkillForge edits survive.

What a deploy would carry: `PALADIN_GRAPH_VERSION` 6→7, `VOID_GRAPH_VERSION`
3→4, `SKILL_GRAPH_VERSION` 14→15, new `INFINITY_HERO_GRAPH_VERSION` 1.

```bash
cd /opt/infinity/server && git pull && python -m seed && sudo systemctl restart infinity-game infinity-api
```

Before that, on the VM, confirm the seed reports the new lines
(`infinity_hero_skills=5`, `monster_skills_linked` up by 2) and that
`test_port_parity.py` still passes against the deployed tree.

Rolling back is a `git revert` plus the same reseed — the version bumps are the
only persistent effect, and the Python combat path is untouched, so live combat
behaves identically either way.

### After deploying, worth doing in-game

- Equip item 200022 and confirm the Infinity Hero icons rebind as you chain
  Aspects, and that the bar arms at 25.
- Visit the Abomilich placement (map 2239/2241) and confirm the boss is now
  visible and telegraphs.
- The aura magnitudes in gap 2 are the ones most worth feeling out live; they
  are one JSON edit away from a change.
