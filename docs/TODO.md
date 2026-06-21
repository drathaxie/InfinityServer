# InfinityServer — TODO (needs confirmation / in-game testing)

Items here either change visible behavior (so they need an in-game check) or are
decisions only you should make. Everything I could verify with unit tests alone has
already been done (see "Done autonomously" at the bottom).

## Needs in-game verification

- [ ] **Skill particles + Dragonbane aura VFX render.** Fixed the root cause (class
  particle bundle `ClassParticleBundle` was being stripped; now `eqp.Class` comes from
  the authoritative `classes.rig`). Relog and confirm the big Dragonbane flame + per-skill
  particles show. If they still don't: confirm AE's CDN serves
  `gameassets/classes/78052_classdragonslayer_default.unity3d` (assets stream direct from AE).
- [ ] **Stat-based damage — REFIT to capture (P1-2), confirm in-game.** `_hit` now rolls a
  weapon term `U(ap*1.8, ap*2.5) * skill-mult` (sp for magical) so an auto lands ~56-78 at
  ap 31 (the captured band; was ~26-36). tcr/scm refit to the captured `sta` (crit now ~1%,
  not ~11%). **In-game:** auto numbers ~56-78, skills scale up by their multiplier; crits
  clearly rarer. Coefficients are still ours (AE's formula uncaptured) — tell me to retune.
- [x] **Stat-based player HP — REFIT (P1-2).** MaxHP is now `300 + END*56 + level*20` (was
  `200+40*END+30*lvl`, ~30% low); an END18 character ≈1337 (the captured reference). Confirm
  the HP bar looks sane in-game.

## The one real bandaid — RESOLVED (P1-1): real spatial hitbox handshake shipped

- [x] **`AnimationHitbox`/`Hitbox` now use the real igai/gai handshake (you chose the faithful
  path).** The premise that "the client never sends the gai" was wrong — the capture has the
  capture account's own AnimationHitbox/Hitbox casts with matching gai answers returning 1-3
  monsters (real cleave). Engine: AnimationHitbox/Hitbox back in `INPUT_NODES`; `_igai` emits
  the exact captured shapes (AnimationHitbox RT1, Hitbox RT2); `resume_cast` cleaves Damage
  across the returned set; removed the PlayerAnimation double-anim workaround from the DS
  hitbox skills. **In-game:** cast a melee hitbox skill with 2+ monsters in the arc → multiple
  damage numbers; body animation plays once. If a skill stalls right after the AnimationHitbox
  igai, capture the client's c2s and diff our igai shape (only residual risk).

## Decisions only you can make

- [ ] **Per-skill "Determined" effects — DONE w/ visuals, confirm in-game.** Scorched =
  **3 separate Damage hits** (triple-strike, 3 numbers), Impale = heal 15% max HP (now an
  **Immediate Health Damage node** so the green number renders), Incapacitate = 3s stun
  shown as a **"Stunned" aura on the target** + the monster AI can't act while stunned
  (the Restrict node only locks the caster — confirmed from capture). Confirm the 3 effects
  show in-game. **Dragon's Bane +20%/+50% vs Dragonkin — DONE (P2-3):** as Dragonslayer, hit a
  Dragonkin monster → +20% damage (passive); cast Dragon's Bane → +50% for 10s + 2× Determination
  gain. (Impale's "+amount based on END" is unquantified in the tooltip — left out.)
- [x] **Real base-class IDs — DONE (P2-2) for Healer/Warrior; Mage needs capture.** Mined the
  real ClassIDs by correlating each initPlayer's ClassID with its sClass: **Healer 17, Warrior
  33** (DS 1932 already real). Migrated classes/class_skills/characters (idempotent). **Mage
  never appears in the capture, so its real ClassID is unknown — kept placeholder 2 (not
  guessed).** To finish Mage: log in as Mage in-game and read `playerInfo.ClassID`, then add it
  to `seed.REAL_CLASS_IDS` + `data/classes.json`. (Aside: ClassID 1888 = "Legion Revenant", a
  5th class the capture account had — not one of our four.)

## Lower-priority hardening (safe to do, just not yet)

- [x] **Monster damage `random 12–34` flat — FIXED (P1-3).** Now scales with level
  (`U(level*5, level*9)`, avg ~7*level, from the capture). The authored cell entities carry
  `Level`/`MonID`/`sRace` so the server learns each monster's level. **In-game:** lvl-2 monster
  chips ~10–18, lvl-8 hits ~40–72, higher levels hurt. (Boss multiplier + per-monster respawn
  still flat — flagged.)
- [x] **`statUpdate` after a kill resets the HP bar to full — FIXED (P0-3).** `build_stat_update`
  now carries current HP (`combat.player_hp(uid)`); login still defaults to full. **In-game:**
  take damage, land the killing blow → HP bar stays where it was.
- [ ] **Monster respawn is a flat 8s** regardless of monster type.
- [ ] **Monster AI graphs** (`Node.MonsterInput`: Tile waves/clusters) unimplemented —
  monsters don't use authored attack patterns, just a basic swing.
- [ ] **Aura DoT/HoT + debuffs — DONE (P2-4), confirm in-game.** Bleeding/Scorched tick damage
  (type-5 DoT), Radiance ticks heals, Weakened/Inhibition cut the target's damage 10%. **DoT
  type-5 is NOT in the capture (0/48k)** — the tick mechanic + amounts are ours (design); if AE
  renders DoT client-side, our server ticks may double-show (watch + switch to client-rendered).
  **In-game:** Impale → red ticks on the monster; Healing Word → green ticks on allies;
  Incapacitate → monster hits ~10% softer.
- [ ] **Pattern/gem stats** may be placeholder — verify the gem stat math is meaningful.

## All four classes are now in (confirm in-game)

- [ ] **Other classes (Healer/Mage/Warrior) have real skills now.** Their skill node-graphs
  were mined from the capture (`capture/extract_skill_graphs.py` -> `data/skill_graphs.json`)
  and seeded data-driven for every skill (DS still uses its hand-tuned graphs). Each class's
  skills cast with their real animations/particles/sounds/auras/damage. **To play a class:
  equip its class armor** — every character was granted the 4 base class armors, and equipping
  one switches class + skills (persists). Confirm each class casts/looks right in-game.
- [ ] **Crit popups** now show (resolved `DamageTypes` = popup kind: 1=Crit, 0=Normal).
- [x] **Miss/Dodge popups — ADDED (P0-4), confirm in-game.** Attacks roll to-hit from `tha`
  (player ~1% miss) + a rare dodge; monsters miss ~7%. Miss/dodge deal 0 (`DamageTypes` 3/2).
  **In-game:** occasional MISS/DODGE popups. (Crit *rate* still high until P1-2 retunes `tcr`.)
- **Per-class refinements still needed** (the engine is generic; these are content/mechanics):
  - **Per-class resources — FIXED (P0-2), confirm in-game.** DS = Determination (white bar,
    orange at 50); Mage/Healer/Warrior = mana (blue bar, no threshold) that drains on cast and
    refills on auto-attack. Stored in `classes.resource`; updateClass sent on login + class
    switch. **In-game:** DS bar white→orange at 50; mana bar blue, drains casting an act=0 skill
    (Fireball/Holy/Heartbeat/Decisive Strike). NOTE: act=2 Flex skills (Healing Word, Arcane
    Shield, Prepared Strike, On Guard) carry no regMana in the capture, so they don't drain mana
    via RegularMana — their cost (per tooltip) is server-internal; left as a follow-up.
  - **Determined/empower effects are DS-only** (`EMPOWERED_FX`); other classes get the flat 2×.
  - **Healer heals — FIXED (P0-1), confirm in-game.** Capture shows only **Healing Word
    (slot 2 / 142)** heals (negative Damage on self + up to 3 nearby allies); 140 Auto, 141
    Heartbeat, 143 Energy Flow, 144 Holy are offensive (correct as mined). Engine now renders
    a `Damage{Heal:true}` node as a green negative-damage popup that raises ally/self HP;
    positive damage to players stays gated. **In-game:** equip Healer, cast Healing Word on
    self → green heal, HP rises. (Heal magnitude scales on spell power; Multiplier is ours.)
  - **Mage graphs are sparse** (only 1–2 casts were in the capture) — may be incomplete.
  - **Element + multipliers — AUTHORED (P1-4), confirm in-game.** Mage/Healer offensive skills
    now cast **Magical** (scale on spell power → INT/WIS); Warrior is **Physical** (→ STR/DEX).
    Per-skill multipliers by role (auto 1, nuke 2, AoE 1.5) — ours, retune on request. **In-game:**
    Mage damage tracks INT not STR. (Full contrast needs char-create; all chars share template stats.)

## Done autonomously this session (no confirmation needed)

- **Continuous auto-attack** — server-sustained (the AI loop re-fires it on the cooldown
  until the target dies or you leave), so you no longer press it each time. Slot 0 is now a
  clean single-shot (no handshake stall); client + server share the cooldown gate (no double
  fire). Stops on cell/map change or target death.
- **Per-skill Determined effects** — Scorched ×3, Impale self-heal, Incapacitate stun
  (monsters can't act while stunned). Confirm in-game (see above).
- Stat-based damage + HP (replaced the fake `18–55` roll); magical uses spell power.
- Authoritative per-class visual rig in `classes.rig` (fixes the particle bundle properly,
  works for every class — Mage/Healer/Warrior/Rogue/Dragonslayer rigs mined from capture).
- `sEAct` particle preload now includes AuraVFX names (`<VFX>_Appear/_Exit`).
- Class persists on equip (`characters.class_id`); "No Class" fixed (ClassID falls back to
  the template's instead of 0); `user.sClass` set.
- Combat stops when you change cells/maps (`drop_aggro_for` on `moveToCell`/`tfer`).
- Skill body animations via `PlayerAnimation` (reliable); determination build→spend→empower.
- Equipped gear persists on relog (`user.eqp` built from `char_items`).
