# InfinityServer — Combat + Class Systems Audit

**Date:** 2026-06-17
**Scope:** `server/{combat,forge,game,seed,server}.py`, `data/{skill_graphs,class_rigs,class_item_defs,classes}.json`, `capture/extract_skill_graphs.py`.
**Ground truth:** `docs/decomp/*` (decompiled client) + live capture `…\UserData\Beyond\packets.jsonl` (115 MB, ~48k pkts).
**Method:** every previous-session claim was re-checked against decomp source lines and/or captured packets. Claims that could not be re-derived from either are marked **Unverifiable (needs in-game)**. The previous session's own notes were treated as suspect, not as evidence.

> **Headline:** the *protocol / authoring / persistence* layer is largely faithful and correct. The *combat simulation* layer is where the shortcuts live — and several are not "necessary invention," they are demonstrably contradicted by the capture (crit/miss types, per-class resource model, spatial hitbox AoE, healer heals, monster damage). One frequently-repeated claim ("Aura was dropped from Warrior Decisive Strike 115") is **false** — the capture shows 115 has no Aura — but verifying it surfaced the real defect (115 is a multi-target hitbox cleave the server can't reproduce).

Legend: ✅ Correct (faithful to AE) · 🩹 Bandaid (works, not faithful) · ❌ Broken (wrong behavior) · ❓ Unverifiable (needs in-game/more capture)

---

## A. Combat node + streaming engine (gar / gai / igai / gas, StatusCode)

| Item | Status | Evidence |
|---|---|---|
| `gar`→walk graph; `igai` for input nodes; `gai` resumes; `Attack{Caster,Slot,StatusCode,Nodes}` | ✅ | Matches `CombatPlayer.cs:56` (`ProcessNodes`), `ResponseAttack.cs`, `ResponseAttackInput.cs:34-55`. Single-shot vs streaming split (`combat.begin_cast` `server/combat.py:397`) is a valid simplification of the same wire. |
| Resolved `Damage` node shape `{DamageTypes,Damages,Targets,TargetHPs}` | ✅ | `NodeDamage.cs:12-15`; capture Damage packets match exactly. |
| `Resource` node reports an **absolute** post-cast total | ✅ | `NodeResource.cs:18` `caster.RP = num;` (not additive). `combat._render_node` Resource branch (`combat.py:176-182`) reports the total. Correct. |
| StatusCode uses only `Success(1)`/`Pending(2)` | 🩹 | Enum is `Fail,Success,Pending,Header,Branch` = 0..4 (`ResponseAttack.cs:8-15`). Engine has `NS_SUCCESS, NS_PENDING` only (`combat.py:296`). No `Branch(4)`/`Header(3)` path. |
| **Conditional / Branch skills cannot execute** | ❌ | `ResponseAttack.cs:63-67`: `Branch` re-enters `ProcessNodes(...,-1,false)`. Palette has Conditional/Activator/BranchOutput component types (memory: NodeLayout `ComponentType` 4/5/6) but `_walk_graph` (`combat.py:95-111`) follows a single linear `Next` chain only — any conditional graph is silently linearized. Provable from decomp; no captured branch skill to diff. |
| `gas` / `RequestAttackStream` ignored | 🩹 | `RequestAttackStream.cs` (`Cmd="gas"`) exists; `server.py:336` treats `gas`/`startCharge`/`cancelCharge` as no-ops. OK for Regular/Auto skills; **breaks Hold/Channel** (`Skill.ActionType.Hold/Channel`, `Skill.cs:6-14`; `NodeChannel`, `NodeMaxSkillHold`, `NodeStopChannel` exist). No Channel skill in the seeded set yet, so low impact today. |

---

## B. Hitbox / AnimationHitbox / AoE  ← **the core combat bandaid**

| Item | Status | Evidence |
|---|---|---|
| `INPUT_NODES` — AnimationHitbox/Hitbox resolved server-side onto the **cast target** | ✅ FIXED (P1-1) | `INPUT_NODES = {"Range","RangeMulti","AnimationHitbox","Hitbox"}`. AnimationHitbox/Hitbox now go through the real igai/gai handshake; `resume_cast` parses the **set** of returned targets into Damage → multi-target cleave. (Dash/DashToTarget still render inline, per capture.) |
| **Real skills are multi-target hitboxes** | ✅ FIXED (P1-1, capture-grounded) | Capture (own-cast): the `AnimationHitbox` gai returns 1 (327×), **2 (45×), or 3 (11×) monsters** — real cleave. Engine reproduces it: `test_combat.py` P1-1 drives `gar→igai(Range)→Pending→igai(AnimationHitbox RT1)→gai(3 monsters)→Damage` hitting all 3. |
| Root cause of the original stall was **not** root-caused | ❌→📌 **CLAIM REVISED (2026-06-17 capture)** | The capture contains the capture account's OWN AnimationHitbox/Hitbox handshake, and the client **does** answer the gai (asynchronously, after the box frame). Counts: `igai AnimationHitbox ReturnType=1` ×370, `igai Hitbox ReturnType=2` ×92; matching `gai [slot,ctx,"AnimationHitbox",...targets]` answers with the SAME ContextId — **returning 1 (327×), 2 (45×), or 3 (11×) targets** (real cleave; `'actor'` = caster self, filtered). So "the client never sends a gai for AnimationHitbox" is **false** — the previous session likely had a wrong igai shape/timing, not a protocol gap. No `hitboxes` packet exists in the capture (mapHasGeometry=false on these maps), so the geometry registry path is not needed here. **This de-risks P1-1: the faithful handshake is fully captured and `resume_cast` already parses multi-target gai.** |
| `Hitbox` (vs AnimationHitbox) handshake | ✅ FIXED (P1-1) | Both answer a `gai` (capture: Hitbox igai `ReturnType=2`, AnimationHitbox `ReturnType=1`). The server now emits each with its captured shape — Hitbox carries the box + `inputReturn:2` (no Animation/Speed/Time); AnimationHitbox carries box + Animation/Speed/Time + `inputReturn:1`. |
| Server never sends `hitboxes` (geometry registry) | ❓ (not needed on captured maps) | **No `hitboxes` packet appears anywhere in the capture** → `mapHasGeometry=false` on every captured map, so the client's BoxCastAll runs without server geometry and the gai returns hits fine. `ResponseHitboxes`/`ServerHitboxRegistry` only matter on geometry maps; deferred until one is confirmed in-game. |

---

## C. Damage formula & combat stats (`game.build_combat_stats`)

Captured ground truth (`statUpdate`, `p:21675187`): `ap=31, sp=31, tcr=0.0114, scm=1.538, STR14 END18 DEX12 INT11 WIS10 LCK13, HP=MaxHP=1337`. Also `statUpdate` carries `DmgMin/DmgMax` (`ResponseStatUpdate.cs`).

| Coefficient | Server (refit P1-2) | Server value @ captured stats (lvl1) | Real | Status |
|---|---|---|---|---|
| `ap` | `10+STR+0.3·DEX+2·lvl` | ≈30 | 31 | ✅ close (unchanged) |
| `sp` | `10+INT+0.3·WIS+2·lvl` | ≈26 | 31 | 🩹 still diverges (left for P1-4; element/stat design) |
| `tcr` (crit %) | `0.0005·LCK+0.0004·DEX` | **0.0113** | **0.0114** | ✅ FIXED (P1-2) — was ~0.113 |
| `scm` (crit mult) | `1.5+0.003·LCK` | 1.539 | 1.538 | ✅ FIXED (P1-2) — was 1.63 |
| `MaxHP` | `300+56·END+20·lvl` | 1328 | 1337 | ✅ FIXED (P1-2) — was ~950; now consistent with `PLAYER_MAXHP` |
| element→stat | `DamageType=="Magical"?sp:ap` | — | not on the wire | ❓ element is server-internal (never in capture) |

**Magnitude check (capture):** auto-attacks deal **56–78**, slot-1 skills **126–157**. ✅ FIXED (P1-2): `_hit` now rolls a weapon term `U(ap·1.8, ap·2.5)·mult` (sp for magical) → auto (mult 1) at ap 31 lands **56–78** (verified band [56,77] over 20k rolls); the statUpdate now carries `DmgMin/DmgMax` (= the weapon range). Was `ap·mult·U(.85,1.15)` ≈ 26–36 (~half). The weapon range is OUR model (AE's real `DmgMin..DmgMax` was null in the capture sample).

- Provable from capture: tcr 10× off, scm high, MaxHP off, magnitude ~½ — all **fixed and asserted against the captured `sta`** (`test_combat.py` P1-2). The exact AE formula is **not** capturable; faithful = coefficients now PRODUCE the captured `sta` for the captured stat line + a weapon term. `sp` divergence (ap≈sp in real data) is deferred to the element/multiplier authoring in P1-4.

---

## D. Damage-type popups (Normal/Crit/Dodge/Miss/Blocked/DoT)

| Item | Status | Evidence |
|---|---|---|
| Enum `Normal0,Crit1,Dodge2,Miss3,Blocked4,DoT5` | ✅ (mapped) | `BattleTextBouncer.cs:10-18`. |
| Server emits only `0`/`1` (Normal/Crit) | ✅ FIXED (P0-4) | `_hit` now returns `(dmg, dtype)` with dtype ∈ {0 Normal, 1 Crit, 2 Dodge, 3 Miss}; a miss/dodge deals 0 (`Damages:[0]`, HP unchanged). Per-target, so multi-hits carry per-target popups. |
| Real server emits Dodge & **Miss** | ✅ FIXED (P0-4, capture-grounded) | Re-verified histogram (2026-06-17): `0`→8285, **`3`(Miss)→330**, `1`(Crit)→221, `2`(Dodge)→5. Server now rolls to-hit from `sta.tha` (a player at tha 0.9895 misses ~1%), a rare `DODGE_CHANCE` evasion (type 2), and crit from `tcr`. Monster swings miss at `MON_MISS_CHANCE=7%` (monster to-hit isn't captured; lands the overall ~4% miss). `tha` now flows: `game.build_combat_stats`→`sta.tha`→`combat.set_power`. |
| DoT(5)/Blocked(4) | ✅ DoT built (P2-4, design — see §P2-4); Blocked(4) still unused | DoT/HoT now tick server-side (`aura_ticks`, type-5 Damage). **Capture caveat: type-5 is 0/48k in the capture** — AE's DoT damage isn't on the wire as type-5 (server-internal or client-rendered), so our type-5 ticks + amounts are a DESIGN choice, not 1=1. Blocked(4) still never emitted (no block mechanic). |

---

## E. Resource model — Determination applied to **all** classes

| Item | Status | Evidence |
|---|---|---|
| Resource bar is **per-class** (`RP/MaxRP/Threshold/ThresholdColor/ResourceColor`) | ✅ (in client) | `ResponseClass.cs:5-13` (`Cmd="updateClass"`); `UIPlayerPanel.setSlider` `:170-198` colors the bar by `ResourceColor` and swaps to `ThresholdColor` at `Threshold`. |
| **Two distinct resource models in real data** | ✅ Correct | Captured `updateClass` re-verified (2026-06-17): **Dragonslayer** → `MaxRP100, Threshold50, ThresholdColor=16745728 (0xFF8000 orange), ResourceColor=16777215 (0xFFFFFF)` = Determination. **Other classes** → `MaxRP100, Threshold=-1, ResourceColor=255 (0x0000FF blue)` = a mana pool with **no** threshold. Stored per-class in `classes.resource` (seeded from `data/classes.json`). |
| Server hardcodes one model for everyone | ✅ FIXED (P0-2) | Was `server.py:385-387` (blue for everyone, no updateClass on login). Now: `forge.resource_for_class`/`build_updateclass` build the class's real bar; login **and** class-switch send it (DS white/orange-at-50; mana classes blue). `combat._apply_determination` is model-aware — DS builds Determination, mana classes spend/restore (`_apply_mana`); `set_resource_model`/`set_class_mana` set per-uid state on login/switch. |
| Skills carry a **mana cost** (`RegularMana`) | ✅ FIXED (P0-2, needs in-game confirm) | `Skill.cs:36,39,64` (`mana`, `regMana*-1`); `SkillSlotButton.checkMana` gates on it. Mana classes now **spend** on cast: cost = `max(0,-regMana)` (`forge.class_mana_costs`), reported via the Resource node (drains the bar); the auto-attack restores `AUTO_MANA_REGEN=10`. **Capture finding: `regMana` is present only on `act=0` Regular skills (Holy -20, Heartbeat -10, Fireball -15…); `act=2` Flex skills (Healing Word, Arcane Shield, Prepared Strike, On Guard) carry NO regMana** — so Flex skills don't spend via RegularMana (their cost, if any, is server-internal). Faithful to capture; the act=0 skills drain the bar. |

DS determination *accumulation* itself is faithful: captured `Resource` amounts `5,15,20,70,…` = auto+5 / skill+10 / Bane+50, which `combat.py` reproduces. The bug is applying it universally and sending the wrong `updateClass`.

---

## F. Per-skill "Determined" empower effects

| Item | Status | Evidence |
|---|---|---|
| Scorched ×3 / Impale self-heal / Incap stun (DS) | ✅ verified vs tooltips (needs in-game) | The trigger values now match the captured tooltips (`data/classes.json` `_skilldefs`): Scorched "Strike **3** times" = `hits:3`; Impale "Heals you for **15%** of your max HP" = `pct:0.15` (the "+amount based on END" is unquantified → left as a design extension); Incap "Stun for **3** seconds" = `secs:3.0`. The flat ×2 fallback is now dead (mana classes don't empower; all DS skills are listed). |
| Dragon's Bane "+20% vs dragons" | ✅ FIXED (P2-3) | `_dragon_bonus`: a Dragonslayer deals **+20%** to Dragonkin-race monsters (passive), **+50%** while the Dragonbane buff is active (`_dragonbane[uid]`, 10 s, set when 105 is cast); Dragonbane also **doubles** Determination gain. Race comes from `register_monster` (P1-3); the catalog has 11 `Dragonkin` monsters. Gated to DS via the determination resource model. |
| Empower is **DS-only**; other classes get flat ×2 | 🩹 | `_empower` falls back to `EMPOWER_MULT=2.0` for any non-DS skill (`combat.py:527-538`). Warrior Rage / Mage Frozen Blood / Healer mana empowers are unbuilt. |

---

## G. Skill-graph mining (`extract_skill_graphs.py` → `data/skill_graphs.json`)

| Item | Status | Evidence |
|---|---|---|
| Structure (animations, particles, sounds, cooldowns, ranges, hitbox dims, auras, restricts) | ✅ mostly | `KEEP` map (`extract_skill_graphs.py:51-64`) preserves these; spot-check of 115/141/136 matches capture node order. |
| **Damage `Multiplier` forced to `1.0`, element forced `Physical`** | ✅ AUTHORED (P1-4) | `authored()` still emits the lossy `{Physical, 1.0}` placeholder (neither value is on the wire), but `seed.SKILL_DAMAGE` now overrides element + multiplier per skill at seed time (Mage/Healer→Magical/`sp`, Warrior→Physical/`ap`; multipliers by role) — `skill_graphs.json` stays pure mined structure, the damage scaling is hand-authored like the DS five. |
| Aura helper wiring is a heuristic | 🩹 | `:70-73` classifies an Aura as Self vs Target by "all targets are `p:`" — fragile for ally-targeted or no-target casts; drops `Duration`/`uniquenessType`. |
| Unknown node types silently **dropped** | 🩹 | `authored()` returns `None` for anything outside `KEEP`∪`{Damage,Aura}` (`:79`) → e.g. `Channel,Hit,InstantDamage,HitStream,Dash,SkillGlow,DisableSkill,…` vanish. |
| Only the **first clean cast** per skill; truncates at `StatusCode==1` | 🩹 | `:42-48`. A node dispensed *after* Success (late aura/DoT) is lost; an empowered first cast would be mis-recorded. |
| **"Aura dropped from Warrior Decisive Strike (115)"** | ❌ **CLAIM FALSE** | Direct capture diff: **53** casts of 115, **zero** Aura nodes. Real 115 = `Range,Resource,Cooldown,Restrict,Interruptable,SoundFX,Particle,AnimationHitbox,Damage,DispenseDamage,UpdateAnimation` — exactly what `skill_graphs.json[115]` contains. The mined 115 is structurally faithful. (The *real* 115 defect is the AoE hitbox in §B, not a missing aura.) |
| `skill_graphs.json` contains `153 "Info"` (not in any class slot) + the DS five (ignored by seed) | 🩹 | Harmless noise; `seed_skill_graphs` skips DS ids (`seed.py:394,402`) and only seeds ids that exist as skills. |

---

## H. Healer — heals are inverted into damage, then suppressed

| Item | Status | Evidence |
|---|---|---|
| Real Healer heals via **negative-damage** Damage nodes on allies | ✅ Correct (needs in-game confirm) | `BattleTextBouncer.cs:279-326`: `HP<0` → `popupHeal`/`popupHealCrit`/`popupHOT` (green). **Re-derived capture (2026-06-17): heals are ONLY Slot 2** — 194 negative-damage Attacks at slot 2, **zero at slot 0/1**; up to 5 `p:` targets, e.g. `[-345]→[p:self]`, `[-342,-342,-342,-342]→4 players`. Slot 2 = **Healing Word (142)**. The earlier "140/141 heal" claim is **disproven** — 140 (basic attack), 141 Heartbeat ("Deals damage…"), 143 Energy Flow (debuff), 144 Holy (smite) are all offensive per their tooltips and carry no negative-damage casts. |
| Server makes Damage **never target players** | ✅ FIXED (P0-1) | `combat.py` `_render_node` now splits heal vs damage: a Damage node with `Heal:true` produces **negative** `Damages` on ally/self `p:` targets (raised `TargetHP`); offensive positive damage is still gated away from players (anti-self-kill). Heal targets resolve via an `Allies` helper (caster + nearby players, capped at `HEAL_MAX_TARGETS=4`); allies threaded from `server.py` (`_area_allies`). |
| The self-kill it was avoiding | (diagnosed) | A *positive* Damage with `TargetHPs:[0]` on a player → client sets HP and dies (`NodeDamage.cs:102,120` set `entity.HP = TargetHPs[i]` for non-main-player casters; `BattleTextBouncer.cs:395-398` → `DelayedDeath`). The fix is not "never target players" — it's "heal = negative value raises HP; only *hostile* positive damage is gated." |
| Mined Healer graphs are authored as **offensive Physical** | ✅ FIXED (P0-1, needs in-game confirm) | `extract_skill_graphs.py:67` forces every Damage offensive; the heal sign is not minable. **Healing Word (142)** re-authored in `data/skill_graphs.json` as a heal (`Damage{Heal:true, Multiplier:10, Targets:Allies}`), `SKILL_GRAPH_VERSION` bumped to 4 to re-seed. Heal scales on the caster's **spell power** (`sp`) — Multiplier is OURS (AE's heal formula is server-internal/uncaptured), tuned to land in the captured ~120-650 band. 140/141/143/144 correctly stay offensive. |

---

## I. Mage graphs

| Item | Status | Evidence |
|---|---|---|
| Mage skills cast (structure present) | ✅ | `skill_graphs.json` 135-139 have full node lists. |
| Sparse capture | ❓ | Previous note "1–2 casts." Not separately re-counted here; the first-clean-cast extractor (§G) means any skill with one noisy cast is suspect. Needs in-game check per skill. |
| Mage damage is Physical/`ap` | ✅ FIXED (P1-4) | `seed.SKILL_DAMAGE` authors element per class onto the mined graphs: Mage 135-138 + Healer 140/141/143/144 → `Magical` (combat scales on `sp`→INT/WIS); Warrior 114-117 → `Physical` (`ap`→STR/DEX). Multipliers by role (auto 1, nuke ~2, AoE ~1.5) — ours, not minable. Healing Word stays a heal. `SKILL_GRAPH_VERSION=6` re-seeds. |

---

## J. Class data, seeding, items

| Item | Status | Evidence |
|---|---|---|
| Class-armor **item IDs** (582 DS, 15651 Healer, 15652 Rogue, 15653 Mage, 15654 Warrior) | ✅ | Confirmed against capture (`EquipSpot:6` items with these IDs+names). `class_rigs.json`/`class_item_defs.json` match. |
| Class **rigs** (skin Bundle + ClassParticleBundle) | ✅ | `class_rigs.json` bundle filenames/IDs match captured eqp.Class. |
| **ClassID 1/2/3** for Healer/Mage/Warrior | ✅ FIXED (P2-2) for Healer/Warrior; Mage uncaptured | Mapping **mined** by correlating each initPlayer's `playerInfo.ClassID` with its `user.sClass`: **Healer = 17** (sClass "Healer", item 15651), **Warrior = 33** (sClass "Warrior", item 15654), DS 1932 (already real). 1888 = "Legion Revenant" (a 5th class, not ours). **Mage (item 15653) never appears in the capture** — the account never played it — so its real ClassID is genuinely unknown; it keeps placeholder **2** (NOT guessed, per the project rule). `seed.REAL_CLASS_IDS={1:17,3:33}` remaps classes/class_skills/characters once (idempotent). |
| Class-item def seeding | 🩹 | `seed.py:230-235` `INSERT…ON CONFLICT DO UPDATE SET raw,name` — re-overwrites catalog 582/1565x on every boot. Now uses real `class_item_defs.json` (the login-hang fix), so non-destructive *as long as that file stays correct*, but it still clobbers any in-game edit to those items. |
| Other `ON CONFLICT DO UPDATE` | ✅ | shops/quests/maps/apops/classes upserts are catalog refreshes keyed by id — appropriate (`seed.py` _seed_shop/_seed_quest/seed_maps/seed_apops/seed_classes). The skill-graph seeder correctly *skips* non-empty graphs unless `SKILL_GRAPH_VERSION` bumps (`seed.py:382-385`), so it won't clobber Forge edits. |
| **Class-item `Quantity` = class points (CP), modeled as stack count** | ✅ FIXED (P2-1) | `InventoryItem.cs:114-116` `classRank = new Rank(Quantity)`; `Inventory.hasClassPoints` = `Quantity >= points`; `Rank.cs` caps at **302500**. Fix: (a) `game.sell`/`remove_item` **reject class items** (`_is_class_item` via `isClass`/`EquipSpot==6`) — no more CP decrement. (b) `_grant_item`/`grant_class_items`/buy grant the **maxed CP 302500** (consistent, fully playable). (c) `grant_class_items` **reconciles** every owned class item to 302500 (healed the live `1 / 302499 / 302500` split → all 302500). (d) the catalog/shop `Quantity` is the purchase value **1** (CP is per-instance), which also fixed the long-red `test_shops`. CP *progression* (earning ranks) is a future mechanic — flagged. |
| `next_char_item_id` / dedupe | ✅ | Counter healed (`12055` vs MAX `12054`); no duplicate class-item rows in live DB. `grant_class_items` dedupe (`seed.py:439-447`) works. |

---

## K. Monster AI / respawn / death / kill-reward

| Item | Status | Evidence |
|---|---|---|
| Autonomous aggro loop, monster swings on its own timer | ✅ design | `server.py:590-638` `ai_loop`; `combat.engage/engagements/monster_attack`. Reasonable. |
| Monster damage = flat `random 12–34`, same for every monster | ✅ FIXED (P1-3) | Monster swing now **scales with level**: `damage = U(level·5, level·9)` (`_monster_dmg`), avg ≈ 7·level — derived from the capture (107 monsters: m:2027 lvl2 ~12, m:968 lvl8 ~56, m:1979 lvl12 ~71; dmg/level ≈ 6–7). `register_monster` stores `level`/`race`/`element`; the authored CellJoin entities now carry `Level`/`MonID`/`sRace` (placements), as the real captured entities did. Flat `MON_DMG` is the fallback when level is unknown. Bosses (e.g. m:1553 ~15/level) would under-hit — a boss multiplier is a refinement. |
| No monster skill graphs (`Node.MonsterInput` Tile waves/clusters) | ❌/❓ | `Node.cs:211-241` defines 7 MonsterInput resolvers; none implemented. Bosses in capture likely use them. Needs capture mining + in-game check. |
| Flat 8 s respawn for all | 🩹 | `combat.RESPAWN_DELAY=8.0` `:23`; per-monster timing not modeled. |
| Player death → instant full-heal revive | 🩹 | `combat.revive_player` `:684-690` full-heals; `ai_loop:611-613` revives immediately. Players effectively can't die. |
| **`statUpdate` resets HP to full after every kill** | ✅ FIXED (P0-3) | `build_stat_update(char, hp=None)` now carries the player's **current** HP; `_handle_kills` passes `combat.player_hp(uid)`. `hp=None` (login / stat refresh) still defaults to full, which is correct. HP clamped to MaxHP. Kill no longer restores the HP bar. |

---

## L. Loose ends

| Item | Status | Evidence |
|---|---|---|
| Unused & wrong `DTYPE` map | ✅ FIXED (P3-3) | Deleted. Element is read via `DamageType=="Magical"`; the resolved `DamageTypes` is the popup kind (0/1/2/3/5). |
| Equip rig keeps template gear for empty slots | 🩹 (intentional) | `game.py:236-241` merges equipped `char_items` over the template `user.eqp` so empty slots keep the Dragonslayer template gear. Prevents a naked avatar but means an unequipped slot shows template gear, not nothing. Acceptable; flag for char-create era. |
| Auto-attack `_roll(18-55)` legacy path | ✅ FIXED (P3-4) | `auto_attack` now routes through `_hit` (stat weapon roll + crit/miss + Dragon's Bane), unified with the graph path. `_roll` survives only as `_hit`'s fallback for unregistered/monster casters. Test: a registered ap-100 auto averages ~214, not the flat ~36. |

---

# Prioritized fix list

Each: **root cause → faithful fix → confirming check.** No code yet.

### P0 — wrong/broken behavior, cheap to verify, high impact

**P0-1 — Healer heals nothing (and would damage allies if unblocked). ✅ DONE (needs in-game confirm).**
- *Root cause:* `combat.py:159-161` strips `p:` targets from all Damage nodes; mined Healer Damage is offensive Physical (`extract_skill_graphs.py:67`).
- *Fix shipped:* `_render_node` now has a heal branch — `Damage{Heal:true}` produces **negative** `Damages` on ally/self `p:` targets, `TargetHP = currentHP + heal` (clamped to MaxHP, tracked in `_php`), `DamageTypes` 0/1 (crit). Positive damage to players stays gated. New `Allies` target helper (caster + area allies, cap `HEAL_MAX_TARGETS=4`); `_heal_amount` scales on `sp`. Healing Word (142) re-authored as a heal; `SKILL_GRAPH_VERSION=4` re-seeds. **Correction to the original audit: re-derived capture shows ONLY slot-2 heals (142); 140/141/143/144 are offensive** — so only 142 was converted.
- *Proven:* `test_combat.py` P0-1 block — heal node yields negative `Damages` + raised `TargetHPs` on `p:` targets, party cap honored, the real seeded 142 graph heals; capture-grounded (slot-2 negative damage, up to 5 `p:` targets). Full suite green (except pre-existing `test_shops`/P2-1).
- *In-game check (TODO):* equip Healer (item 15651), cast **Healing Word (slot 2)** on self → green heal number, HP bar rises. Watch the `RangeMulti` igai handshake resolves (142 has a `RangeMulti` input node; if it stalls that's a separate handshake issue, not the heal).

**P0-2 — Per-class resource model wrong for everyone; `updateClass` hardcoded. ✅ DONE (needs in-game confirm).**
- *Root cause:* `server.py:385-387` sent one model (blue) and only on class-switch (none on login); `combat` ran DS Determination logic for all classes; mana (`Skill.RegularMana`) ignored.
- *Fix shipped:* `classes.resource` column (migration + `data/classes.json` `Resource` blocks, seeded) stores `{model, ResourceColor, MaxRP, Threshold, ThresholdColor}` per class — DS `determination 16777215/100/50/16745728`, others `mana 255/100/-1/-1` (exact capture values). `forge.build_updateclass` sends the real bar on **login and class-switch**. `combat._apply_determination` is model-aware: DS builds Determination, mana classes spend cost on cast + restore on auto (`_apply_mana`); `set_resource_model`/`set_class_mana` per uid. Mana spend reported via the Resource node.
- *Proven:* `test_forge.py` (updateClass shapes per class match capture exactly; mana costs Holy=20/Heartbeat=10; unknown class → blue fallback) + `test_combat.py` (mana starts full, spends per cost, auto restores +10, never empowers; Determination unchanged). Migration verified non-destructive on a copy of the live DB. Full suite green (except pre-existing `test_shops`/P2-1).
- *In-game check (TODO):* equip Dragonslayer → bar **white**, fills to 50 then turns **orange** (Determined). Equip Mage/Healer/Warrior → bar **blue**, **drains** when casting an act=0 skill (Fireball/Holy/Heartbeat/Decisive Strike), refills on auto-attack.

**P0-3 — `statUpdate` heals player to full on every kill. ✅ DONE (needs in-game confirm).**
- *Root cause:* `_handle_kills` + `game.build_stat_update` set `HP=MaxHP`; current `_php` wasn't threaded in.
- *Fix shipped:* `build_stat_update(char, hp=None)` carries current HP (clamped to MaxHP); `_handle_kills` passes `combat.player_hp(uid)`. Login/refresh still default to full (correct).
- *Proven:* `test_combat.py` P0-3 block — `hp=None`→full, `hp=137`→137, over-max clamps; integration: a monster hit drops HP and the post-kill statUpdate keeps the wounded value. Suite green.
- *In-game check (TODO):* take damage from a monster, then land the killing blow → HP bar stays where it was (no snap to full).

**P0-4 — Crit-only popups; no Miss/Dodge. ✅ DONE (needs in-game confirm).**
- *Root cause:* `_hit`/`_render_node` only emitted 0/1; `sta.tha` unused.
- *Fix shipped:* `_hit` rolls the attacker's to-hit (`tha`) → `3 (Miss)` on failure, then a rare `DODGE_CHANCE` → `2 (Dodge)`, then crit; miss/dodge deal 0. `monster_attack` rolls `MON_MISS_CHANCE=7%`. `tha` authored in `build_combat_stats` (~0.99, DEX-scaled) and threaded into `set_power`.
- *Proven:* `test_combat.py` P0-4 block — fed the captured player `sta` (tha 0.9895, tcr 0.0114) over 40k casts: emitted histogram tracks the inputs (Miss ≈ 1−tha ≈ 1.0%, Crit ≈ tcr ≈ 1.2%, Dodge rarest, Normal ≫ all); monster miss ≈ 7%. Suite green. (The capture's overall 3>1 ordering comes from the more-numerous monster swings, reproduced by `MON_MISS_CHANCE`.)
- *In-game check (TODO):* fight for a bit → occasional **MISS** popups (yours ~1%, monsters more often); rare **DODGE**. NOTE: crit *frequency* won't exactly match capture until P1-2 retunes `tcr` (currently ~10× high); the miss/dodge mechanism is correct now.

### P1 — faithfulness gaps the project rule ("1=1, model on real data") demands

**P1-1 — Spatial hitbox AoE (the headline bandaid). ✅ DONE — user chose the faithful handshake (needs in-game confirm).**
- *Root cause (revised):* the previous session removed AnimationHitbox/Hitbox from `INPUT_NODES` believing the client never answered the gai. **The capture disproves that** — the capture account's own AnimationHitbox/Hitbox casts have matching `gai` answers (ContextId round-trips) returning 1-3 targets. The real issue was an unfaithful igai shape, not a protocol gap.
- *Fix shipped:* (a) `INPUT_NODES` += `AnimationHitbox`, `Hitbox`. (b) `_igai` emits the **exact captured shapes**: Range RT0 `{hrange,vrange,mode:validate,target,charge,stayAtMaxRange,type}`; RangeMulti RT0 `{hrange,vrange,target,max}`; AnimationHitbox RT1 `{X,Y,Width,Height,Animation,Speed,Time,inputReturn:1}`; Hitbox RT2 `{X,Y,Width,Height,inputReturn:2}`. (c) `resume_cast` already parses the returned target SET (filters `'actor'`/non-`m:`) → Damage cleaves. (d) Removed the `PlayerAnimation` workaround from the DS hitbox graphs (the body animation is driven by `AnimationHitbox.Input`; capture batches carry no PlayerAnimation — leaving it double-animates). `SKILL_GRAPH_VERSION=5` re-seeds. No `ResponseHitboxes` needed (no geometry maps in capture).
- *Proven:* `test_combat.py` P1-1 — full `gar→igai(Range RT0)→Pending[Range,Cooldown,SoundFX]→igai(AnimationHitbox RT1)→gai(3 monsters)→Success Damage` hitting all 3; Hitbox igai RT2 with no Animation; seeded DS Scorched drives the two-stage handshake. Suite green.
- *In-game check (TODO):* cast a melee hitbox skill (DS Scorched/Impale/Incap, Warrior Decisive Strike) with 2+ monsters in the swing arc → **multiple** damage numbers, not just the clicked one; the body animation plays once (no double-anim). Watch the server log for `igai(AnimationHitbox)` → `gai` → multi-target `Damage`. If a skill stalls after the AnimationHitbox igai, the client isn't answering that gai — capture the exact c2s and diff the igai shape.

**P1-2 — Damage formula vs captured `sta`. ✅ DONE (needs in-game confirm).**
- *Root cause:* invented coefficients (`game.py`); no weapon term.
- *Fix shipped:* refit `tcr = 0.0005·LCK + 0.0004·DEX` (≈0.0113, was ~0.113), `scm = 1.5 + 0.003·LCK` (≈1.539), `MaxHP = 300 + 56·END + 20·lvl` (≈1328). `combat._hit` now rolls `U(ap·WEAPON_MIN, ap·WEAPON_MAX)·mult` (`WEAPON_MIN/MAX = 1.8/2.5`; `sp` for magical); `build_stat_update` carries the matching `DmgMin/DmgMax`. ap/sp split kept; element selects stat.
- *Proven:* `test_combat.py` P1-2 — `build_combat_stats(captured stat line)` ⇒ tcr 0.0113, scm 1.539, MaxHP 1328 (all within tolerance of captured 0.0114 / 1.538 / 1337); 20k auto rolls at the captured `sta` land in band [56,77] (capture 56–78). Suite green.
- *In-game check (TODO):* HP bar ≈1337 for an END18 character; auto numbers ~56–78, skill numbers scale up by their multiplier into the ~120–160 band; crit popups noticeably *rarer* than before (≈1%, not ≈11%).

**P1-3 — Monster damage & identity. ✅ DONE (needs in-game confirm; per-monster respawn deferred).**
- *Root cause:* flat `12–34`; `register_monster` stored no stats.
- *Fix shipped:* `register_monster` now stores `level`/`race`/`element`; `_monster_dmg` scales the swing by level — `U(level·5, level·9)` (avg ≈ 7·level, the captured ratio). The authored CellJoin entities (`placements.cell_entities`) now carry `Level`/`MonID`/`intHPMax`/`sRace`/`strElement` (the captured entity shape), so the `moveToCell` handler learns each monster's level.
- *Proven:* `test_combat.py` P1-3 — lvl2 ≈14, lvl8 ≈56 (capture m:968 lvl8 ~56), lvl12 ≈84; higher level → harder; unleveled → flat fallback; race stored for P2-3. Suite green; a served `battleon` entity now carries `Level=5, MonID=29`.
- *Not done:* per-monster respawn timing (flat 8s remains — respawn cadence isn't cleanly in the capture); boss multiplier (m:1553 hits ~15/level vs the ~7 baseline). Both flagged.
- *In-game check (TODO):* a low-level monster (lvl ~2) chips ~10–18; a lvl-8 monster hits ~40–72; a lvl-12+ monster hurts. Tougher (higher-level) monsters visibly hit harder.

**P1-4 — Mage/Warrior element & multipliers (un-minable data). ✅ DONE (needs in-game confirm).**
- *Root cause:* `extract_skill_graphs.py:67` forces Physical/×1; element & multiplier are server-internal, never on the wire.
- *Fix shipped:* `seed.SKILL_DAMAGE` (a hand-authored table, like the DS five) overrides element + multiplier on the mined Damage nodes at seed time: Mage 135-138 + Healer 140/141/143/144 → `Magical` (combat scales Magical on `sp`); Warrior 114-117 → `Physical` (`ap`). Multipliers by role (auto 1.0, nuke 2.0, AoE/filler 1.5). `skill_graphs.json` stays pure mined structure; `_author_damage` skips heals; `SKILL_GRAPH_VERSION=6` re-seeds.
- *Proven:* `test_combat.py` P1-4 — a caster with `sp 60 / ap 10` deals ~132 with a Magical node vs ~29 with a Physical node (element selects the stat). `test_forge.py` — seeded Holy/Explosion = Magical, Imbalancing = Physical, Healing Word stays a heal. Suite green.
- *In-game check (TODO):* Mage skill numbers scale with **INT** (and a Mage with high INT/low STR out-damages with spells); Warrior scales with STR. Multipliers are ours — tell me to retune any skill that feels off. (Full INT-vs-STR contrast needs char-create stat variation; all chars currently share the template stat line.)

### P2 — mechanics depth / data correctness

- **P2-1 Class-item CP semantics. ✅ DONE (needs in-game confirm).** Class items non-sellable/non-removable (`game.sell`/`remove_item` reject via `_is_class_item`); granted at maxed CP `302500` (`seed.CLASS_CP_MAX` = `Rank.cs` max); `grant_class_items` reconciles every owned class item to 302500 (healed the live `1/302499/302500` split); catalog/shop `Quantity` = 1. *Proven:* `test_economy.py` P2-1 (sell/drop rejected, catalog Quantity 1, owned CP consistent, grant gives maxed CP) + the live red `test_shops` now green; live DB reconciled (all class items 302500). *In-game check:* try to sell/drop a class armor → rejected; all classes' skills available (maxed rank); the HUD shows max class rank. *Deferred:* CP earning/progression (rank-up mechanic).
- **P2-2 Real base-class IDs. ✅ DONE for Healer/Warrior; Mage uncaptured (needs in-game confirm).** Mined the mapping from same-initPlayer `playerInfo.ClassID`↔`user.sClass`: Healer **17**, Warrior **33** (DS 1932 already real). `seed.REAL_CLASS_IDS={1:17,3:33}` migrates `classes`/`class_skills`/`characters` once (idempotent, cascades). **Mage real ClassID is NOT in the capture** (account never played Mage) → kept placeholder **2**, not guessed; needs a targeted capture (log in as Mage, read `playerInfo.ClassID`). *Proven:* `test_forge.py` (Healer 17 / Warrior 33) + migration test (char on class 1→17, stale rows removed, idempotent). Live DB migrated. *In-game check:* equip Healer/Warrior armor → class activates (skin+particles+skills); class-rank requirements resolve against the real ID.
- **P2-3 Determined effects fidelity. ✅ DONE (needs in-game confirm).** Verified the Determined values against the captured tooltips: Scorched ×3, Impale 15% maxHP, Incap 3 s — all match (the flat ×2 fallback is now unreachable). Implemented Dragon's Bane: `_dragon_bonus` = **+20%** to Dragonkin (passive), **+50%** while Dragonbane is active (10 s buff set on cast of 105), which also doubles Determination gain. *Proven:* `test_combat.py` P2-3 — passive ×1.20, Bane ×1.51 vs a `Dragonkin` monster, ×1.0 vs `Human` and for a non-DS caster; casting 105 activates the buff. *In-game check:* as Dragonslayer, hit a Dragonkin monster → ~20% more damage; cast Dragon's Bane → ~50% for 10 s (and the bar climbs ~2× faster). *Unquantified:* Impale's "+amount based on END" (not in tooltip numerically).
- **P2-4 Aura/DoT ticking. ✅ DONE (debuffs grounded; DoT is design — needs in-game confirm).** Built a server-side aura subsystem: `AURA_FX` + `apply_aura` (hooked into the Aura node render) + `aura_ticks()` (driven by the `ai_loop`). **Grounded:** the aura NAMES + durations are real (capture: Bleeding 236, Weakened 167, Scorched 27, Radiance 112, Inhibition 184…); **Weakened/Inhibition −10% target damage** is the tooltip value, applied in `monster_attack`. **Design (flagged):** DoT/HoT *ticks* — `DamageType 5 (DoT)` is **0/48k in the capture**, so emitting type-5 and the tick amounts (a fraction of the caster's power) are OURS, not 1=1. Bleeding (3 s)/Scorched (6 s) DoT, Radiance (5 s) HoT. *Proven:* `test_combat.py` P2-4 — Bleeding DoT (type-5, HP drops), Radiance HoT (negative type-5, HP rises), Weakened ≈−11%, auras expire. Suite green. *In-game check:* Impale → red Bleeding ticks on the monster; Healing Word → green Radiance ticks on allies; Incapacitate → the monster's hits drop ~10%. *Note:* if AE renders DoT client-side (not via a server Damage packet), our server ticks may double-show — watch in-game and switch to client-rendered if so.

### P3 — hardening / cleanup

- **P3-1** Conditional/Branch skill support (`StatusCode.Branch`, `ProcessNodes(...,-1,false)`) so branching graphs execute (`combat.py`, `ResponseAttack.cs:63`). ⏳ **Not done** — no Branch/Conditional skill is seeded yet (the DS "Determined" branch is handled in `_apply_determination`, not via a graph Branch), so zero impact today; build when a branching skill is authored.
- **P3-2** `gas`/Channel/Hold skills (currently no-op). ⏳ **Not done** — no Channel/Hold skill seeded; release fires a `gar`. Low impact.
- **P3-3** Delete the unused `DTYPE` map. ✅ **DONE.**
- **P3-4** Unify the auto-attack fallback onto stat damage. ✅ **DONE** (`auto_attack`→`_hit`).
- **P3-5** Monster `Node.MonsterInput` (Tile waves/clusters) for boss patterns. ⏳ **Not done** — larger subsystem; monsters use the level-scaled basic swing (P1-3). Needs capture mining + in-game.
- **P3-6** Robust skill mining (multiple casts, keep nodes after `StatusCode==1`, don't drop unknown types) in `extract_skill_graphs.py`. ⏳ **Not done** — a mining-tool improvement; current graphs are structurally faithful for the seeded skills.

---

## Verification status summary

| Provable now (decomp + capture) | Needs in-game / more capture |
|---|---|
| Hitbox AoE single-target (115 hits 2 in capture); manual-ReturnType root cause; crit-only popups vs Miss/Dodge in capture; per-class `updateClass` model; tcr 10× / MaxHP off; monster damage flat vs 55–163; Healer heals = negative Damage to `p:`; statUpdate full-heal on kill; class-item CP corruption (1/302499/302500); ClassID 1/2/3 invented; DTYPE unused; Branch/gas unhandled; "115 Aura dropped" **disproven** | Determined effect *values* vs AE tooltips; Mage graph completeness; which maps set `mapHasGeometry`; DoT/Blocked popups; monster skill graphs; base-class ID→class mapping; whether mana classes spend per `RegularMana` exactly |

**Nothing in the 4 mined classes' combat has been confirmed in-game** — all of it was unit-test + capture-diff verified only. Every closed P0/P1/P2 row above carries its exact in-game check.

---

## Fix status (2026-06-17 session)

**Done & committed (capture-grounded + unit-tested; ALL need in-game confirm):** P0-1 Healer heals · P0-2 per-class resource model + `updateClass` · P0-3 no heal-on-kill · P0-4 Miss/Dodge · P1-1 real spatial-hitbox handshake (cleave) · P1-2 damage formula refit + weapon term · P1-3 monster damage scales with level · P1-4 Mage/Healer Magical / Warrior Physical + multipliers · P2-1 class-item CP (non-sellable, maxed, reconciled) · P2-2 real ClassIDs (Healer 17 / Warrior 33; Mage uncaptured) · P2-3 Dragon's Bane +20%/+50% vs Dragonkin · P2-4 aura DoT/HoT + debuffs · P3-3 dead `DTYPE` removed · P3-4 auto-attack unified onto stat damage. **Full test suite 6/6 green.** One git commit per finding (bisectable), on a new local repo.

**Faithfully blocked / deferred (project rule — not papered over):** Mage's real ClassID (capture account never played Mage); DoT type-5 wire shape + tick amounts (type-5 is 0/48k in capture — design); boss damage multiplier + per-monster respawn; Impale's "+amount based on END" (unquantified); `sp` coefficient (uncapturable); P3-1 Branch / P3-2 Channel-gas / P3-5 monster input graphs / P3-6 mining robustness (no seeded content needs them).
