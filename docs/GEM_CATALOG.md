# Complete enhancement gem catalog

The local catalog now covers every family name recognized by `server/patterns.py` across
Weapon (2), Helm (3), Cape (4), and Armor (7): **52 items, including 47 new definitions**.
The gem token itself remains ItemType 43 / EquipSpot 1; its rolled Pattern carries the target
gear slot. Class armor, pets and other unsupported slots are not assigned gems.

| Primary stat | Existing supported family names | Gem items |
|---|---|---|
| STR | Warrior, Fighter, Brute | 12 |
| INT | Wizard, Mage, Sorcerer | 12 |
| DEX | Rogue, Thief, Assassin | 12 |
| WIS | Healer, Cleric | 8 |
| LUK | Lucky | 4 |
| END | Hybrid | 4 |

These are six favored stats with 13 family labels. Balance v2 assigns each family deliberate
supporting-stat weights; Hybrid favors END with STR/INT support. See GEM_BALANCE.md for the
new budget, level/rarity scaling, slot differences and combat simulation. No claim of official
game stat parity is made by these additions.

The five captured items (97549, 97552, 97555, 97556, 97560) retain their IDs and complete
definitions, including authored ItemPattern stats. New definitions use available IDs in the
210000–210051 range and the existing six rarity tiers, Common through Mythic. The generator
rejects occupied IDs and ambiguous family/slot matches. `data/gem_catalog.json` records every
family, primary stat, slot and catalog ID. `scripts/complete_gem_catalog.py` defaults to a dry
run; `--apply` fills missing combinations without changing existing catalog records.

New monster/shop awards use the v2 budget model, including awards from the five old catalog
tokens. Captured catalog definitions remain as source records; owned and already-previewed
patterns are not silently rerolled. Once a new loot gem is shown, that same roll is retained when
claimed or dusted. Shop purchases now deliver `addItems.patternItems` and persist in
`char_patterns`, avoiding unusable ordinary-inventory gem tokens. Gold/coin balances are
updated using the existing native client responses. Gem and gear instance allocation now use
the same atomic allocator to avoid collisions between simultaneous grants.

The existing staff Dev Check All seeding path includes the new items. No new public pricing
or world-drop rates have been invented. The current local global drop table still contains
five captured gems at 5% each; broadening that distribution needs the live catalog/drop audit
before choosing how to preserve its overall reward rate. Dust prices, reroll choices, rarity
upgrades and the Forge workshop are subsequent work.

Production status: **not deployed**. The read-only catalog audit upload was rejected by
automatic approval review because it requires explicit authorization for this additional
source payload and the production destination. Live item-ID collision checks, installed-source
comparison, PostgreSQL regression checks and release application remain pending approval.
Do not import the complete local items file over production: apply only confirmed additions.

Catalog and balance validation: **87 passed, 1 skipped** across the full local suite; focused static checks passed.
Coverage includes all 52 family/slot combinations at all six rarity tiers, generator
idempotency and legacy preservation, gold/coin purchases, equip/swap/dust behavior, retained
loot previews, and concurrent gem/gear instance allocation.
