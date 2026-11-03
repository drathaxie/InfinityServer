# Gem balance v2

This is an authored InfinityServer balance model. Captured examples anchor the level-5
Common weapon's 31 base damage and 24 stat points. The remainder is explicitly designed
here and checked against this server's combat formulas; official hidden formulas are unknown.

## Budgets and progression

The baseline set budget is `40 + 8 * level`. Each slot takes a rounded-up share: armor 35%,
weapon 30%, helm 20%, cape 15%. Individual rounding can put the actual total slightly above
the baseline. Each rarity above Common adds `ceil(CommonSlotBudget / 5)` stat points.
**Power equals STR + END + DEX + INT + WIS + LUK exactly**, for every generated gem.

Level-5 example:

| Slot | Common Power | Mythic Power |
|---|---:|---:|
| Armor | 28 | 58 |
| Weapon | 24 | 49 |
| Helm | 16 | 36 |
| Cape | 12 | 27 |

The model supports levels 1–100; live progression is still capped by `game.MAX_LEVEL = 6`.
Monster drops use the lower of the catalog encounter level and the recipient's current level.
Missing encounter metadata falls back to level 1 when a recipient is present. Shop gem
purchases and Power Up use the owner's level, never a client-supplied level. Gear rolled
outside a live encounter uses its authored item level. V2 gems above the player's level
cannot be applied; rejection leaves both inventory and equipment intact.

Each family has a favored stat and a deliberate supporting-stat profile. Family-specific
focus ranges allow some reroll variation; the highest favored-stat roll of one rarity is
strictly below the next rarity's minimum at the same level, slot and family. Randomness
redistributes supporting stats inside the budget. Off-profile stats stay zero. Supporting
stats cannot overtake the favored stat. A future reroll therefore changes the distribution;
raising rarity is what increases the total budget.

## Combat scaling and family roles

Weapon Base is `ceil((16 + 3 * level) * (1 + .08 * raritySteps))`, with a 10% damage spread.
At level 5, Common is 31 and Mythic is 44, replacing the previous 31-to-230 jump. Helm HP
is `ceil((7 + 2 * level) * (1 + .08 * raritySteps))`; its Base equals HP so the native health
tooltip agrees. Armor/cape Base and HP are zero. Base damage and flat HP are separate slot
properties, not hidden stat points in Power.

- Warrior/Fighter/Brute favor STR with different DEX/END/Luck distributions.
- Wizard/Mage/Sorcerer favor INT with different WIS/END/Luck distributions.
- Rogue/Thief/Assassin favor DEX while retaining substantial STR for physical damage.
- Healer/Cleric favor WIS while retaining INT for spell/healing power.
- Lucky favors LUK and supports both STR and INT; it is a versatile crit profile.
- Hybrid retains the system's END focus and splits offensive support between STR and INT.

The simulation uses `game.build_combat_stats`, `combat.AP_DMG_COEF`, dodge/accuracy and crit
coefficients. It compares 59,904 full sets across all 13 families, six rarities and live
levels 1–6. See GEM_BALANCE_SIMULATION.md and its JSON data for exact figures and constraints.
These results assess unmodified hit expectations. They do not prove equal class DPS, PvP
balance, every skill interaction or in-client presentation; those require gameplay checks.

Local validation completed on 2026-09-11: **87 passed, 1 skipped** in the full server suite.
The balance tests cover 62,400 generated patterns across levels 1–100, all four slots, all
13 families and all six rarities. Acquisition, preview persistence, equipment swaps, dusting,
free Power Up and the read-only migration planner also pass. Focused static checks pass.
These checks used disposable SQLite databases; production PostgreSQL checks remain pending.

## Power Up, dust and existing gems

Free Power Up uses a deterministic Common budget and balanced stats. Its level-5 weapon
retains the captured primary stats and 27–34 displayed damage range. Other levels and slots
now use the same progression model. Repeated Power Up returns the existing applied pattern
without overwriting it. A swapped-out free starter gem has zero Dust, preventing a free
starter-to-dust loop. Random dropped/purchased gems retain the existing rarity Dust ladder.

New monster gem drops and shop purchases use v2 even when an old catalog token contains a
captured static Pattern. Loot already presented to a player retains its exact displayed roll
when claimed or dusted. Existing owned gems are never silently rerolled by loading, applying,
swapping or dusting them. `scripts/plan_gem_rebalance.py` prepares an exact, deterministic,
read-only before/after plan for older owned gems and flags unknown families for manual review.
An existing high-rarity weapon may lose substantial Base under normalization, so a migration
must be reviewed, backed up and applied during maintenance; it has not been performed.

The existing server rarity chances and world-drop rates are unchanged by this balance model.
The broader 52-item catalog release and world-drop coverage still require production audit.

## Release boundary

This balance update is local and not deployed. Production source/catalog comparison was
previously blocked by automatic approval review requiring explicit permission for the new
audit/update upload. A fresh live comparison, PostgreSQL tests, protected backups and service
verification are required before rollout. No existing production gem has been changed.
