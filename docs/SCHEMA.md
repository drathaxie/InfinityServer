# InfinityServer schema — modeled on AE's `AQ2D_Server.Game_Engine.*`

Derived from the 105 MB capture (54k packets): the message vocabulary (48 c2s /
53 s2c Cmds), the `$type` class paths AE emits, and the object shapes of the
major payloads (`initPlayer`, `loadShop`, `getQuests`, `LoadBank`, `AreaJoin`).

Design rule: **one coherent model, three layers.** Stop inventing tables per
feature. Every entity AE has becomes a table; per-character state references the
catalog; the authored world is its own layer.

```
CATALOG (shared "what exists")  ─┐
ACCOUNTS / CHARACTERS (state)    ─┼─ references catalog
AUTHORED WORLD (our content)     ─┘
```

Legend: ✅ exists today · ♻️ exists, expand/rename · 🆕 new

---

## 1. CATALOG — shared content definitions

### `items` ♻️
The item catalog (one row per `ID`). Already normalized. Mirrors the object in
`initPlayer.items[]` / shop items / bank items minus instance fields.
Columns: `item_id PK, name, item_type, equip_spot, level, cost, coins, rarity,
element, faction, stack_size, is_class, raw` (raw = full item def for wire
fidelity; promoted columns for querying). Keep `raw` until every consumed field
is promoted.

### `monsters` 🆕  (replaces the in-memory `montemplates` catalog)
MonID → canonical monster/NPC definition, incl. the art Bundle. Today this is
derived from captured `monBranch` at runtime; persist it so it's editable.
Columns: `mon_id PK, name, subtitle, linkage, hp, level, race, element, behave,
bundle_json, hair_bundle_json, gender, raw`.

### `shops` ♻️ + `shop_items` ♻️
Already done. `shops(shop_id, raw=meta)` + `shop_items(shop_id, shop_item_id,
item_id→items, cost, coins, quantity_remain)`.

### `quests` 🆕 + `quest_turnins` 🆕 + `quest_rewards` 🆕
`Quest`: `quest_id PK, name, desc, end_text, faction_id, class_name, prev_quest,
map_id, dialog_id, apop_id, turnin_type, notification_type, reward_count,
turnin_map_id, turnin_npc_id, turnin_frame, turnin_pad, raw`.
`quest_turnins(quest_id→quests, idx, type, qo_type, qo_id, item_id, quantity,
ref_ids)` — the polymorphic `$type` Turnin / itemTurnin / interactTurnin /
OpenApopTurnin / WatchCutsceneTurnin (one row each, `type` = the subclass).
`quest_rewards(quest_id→quests, kind[static|random], item_id→items, quantity)`.

### `maps` 🆕  (currently `data/maps/<map>.json`)
`map_id PK, area_name, str_map_name, display_name, prefab_name, bundle_json,
soundtrack_id, intType, quest_ids_json, raw`. Cells/geometry stay in the bundle;
this is the AreaJoin metadata. `monBranch` is NOT stored here — it's computed
from the pad layer (below).

### `skills` 🆕 + `classes` 🆕  (Skill Forge — later)
From `sEAct.skillList` + `sf*`. `classes(class_id, name, ...)`,
`skills(skill_id, class_id, slot, name, icon, desc, ranges, node_graph_json)`.

### `apops` 🆕  (dialog/popup — later)
`apop_id PK, raw` (dialogue/cutscene/menu tree).

---

## 2. ACCOUNTS / CHARACTERS — per-player state

### `accounts` ✅
`id PK, username, password, created`. (Add `email`, `access_level` here later.)

### `characters` ♻️ (expand to mirror `playerInfo` + `user`)
Today: `id, account_id, name, gold, coins, level, access_level, user_json`.
Expand into real columns from `playerInfo`/`user`:
`id PK, account_id, name, gender, level, access_level, class_id→classes,
gold, coins, exp, exp_to_level, mq, hp, hp_max, rp_max, upgrade_days,
upgrade_expires, date_created, age, buyer, activation_flag,
str, end, dex, int, wis, lck,                       -- core stats
hair_id, hair_bundle_json, skin_color, eye_color, hair_color,
trim_color, accessory_color,                        -- customization
show_helm, show_cloak, ...prefs...`.
Equipment is the equipped rows in `char_items` (eqp slots), not columns.

### `char_items` ♻️ (rename/expand `inventory`; inventory **and** bank)
One row per owned instance. Bank is the same table (`banked=1`).
`char_item_id PK, char_id→characters, item_id→items, quantity, equipped,
banked, loot_id, char_pattern_id→char_patterns, purchase_date`. Drop the JSON
`raw` (rebuild the wire item from `items` + these instance fields, exactly like
shop listings).

### `char_patterns` 🆕  (`initPlayer.patterns[]`)
`char_pattern_id PK, char_id, pattern`.

### `char_quests` 🆕
Per-character quest state: `char_id, quest_id→quests, accepted, completed,
turn_in_count, quest_bits`.

### `char_factions` 🆕
`char_id, faction_id, rep`.

### `char_friends` 🆕  (`initPlayer.friends[]`)
`char_id, friend_char_id, name, server`.

### `house_items` 🆕  (`initPlayer.houseItems[]`)
`char_id, char_item_id→char_items, x, y, ...placement`.

---

## 3. AUTHORED WORLD — our content layer

### `map_pads` ✅ + `pad_npcs` ✅ + `map_state` ✅
Already normalized into columns (just done). `pad_npcs.mon_id → monsters`.
Compiled into `monBranch` on AreaJoin.

---

## Migration order (incremental, wire output unchanged at every step)

1. **`monsters` catalog** — persist montemplates; `pad_npcs.mon_id` FKs it.
   (Low risk, removes the "derived in-memory" smell first.)
2. **`characters` expansion + `char_items`** — biggest correctness win; replaces
   `user_json`/`inventory.raw` blobs with columns; `initPlayer` rebuilt from them.
3. **`maps` catalog** — move `data/maps/*.json` metadata into the DB.
4. **`quests` (+turnins/rewards) + `char_quests`** — wire up real quest serving.
5. **`skills`/`classes`/`apops`** — Skill Forge / dialogue era.

Each step: add tables, migrate old data in, rebuild the wire payload from the
new tables, keep a `raw` escape hatch only where a field isn't promoted yet,
prove byte-fidelity with a test, then delete the blob column.
