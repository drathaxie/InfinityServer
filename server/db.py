"""
SQLite persistence for InfinityServer. Stdlib only - no external deps.

This is the authoritative store for the private server: accounts, characters,
inventory, gold, and the seeded shop catalog. It has zero connection to
Artix Entertainment - accounts live here and here only.

SQLite is the pragmatic start (single file, zero-ops). The schema is plain
SQL, so moving to Postgres later for a hosted/multi-user deployment is a
connection-string swap, not a rewrite.
"""
import json
import os
import datetime
import sqlite3
import pathlib
import uuid
import atexit
import re
from collections.abc import Mapping

import monrecord
import shoprecord
import qturninrecord
import itemrecord

DB_PATH = pathlib.Path(__file__).resolve().parent.parent / "data" / "infinity.db"

# Which backend persistence runs against. SQLite stays the zero-ops default (single file,
# fast local tests); Postgres is the hosted path. Selected once at import via INFINITY_DB.
BACKEND = os.environ.get("INFINITY_DB", "sqlite").lower()

# On Postgres, tests get isolation via a throwaway schema (the equivalent of SQLite's temp
# DB file) so they never touch the migrated `public` data. "public" = the real database.
_PG_SCHEMA = "public"
_PG_TEST_SCHEMAS = set()

# MySQL has no schema-within-a-database: test isolation uses a throwaway DATABASE instead.
# None = use the configured INFINITY_MYSQL_DB.
_MY_DB = None
_MY_TEST_DBS = set()

SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT UNIQUE NOT NULL,
    password      TEXT,                  -- PBKDF2 hash (pbkdf2_sha256$iter$salt$hash); legacy plaintext upgrades on login
    created       REAL,
    last_accessed REAL,
    session_token TEXT,                   -- random API-issued token the game-server Login must present
    session_version INTEGER NOT NULL DEFAULT 0,
    token_expires REAL NOT NULL DEFAULT 0
);

-- A character mirrors AQ2D's playerInfo + user: identity, currencies, level/exp,
-- class, the six core stats, and customization colours - all real columns.
-- Equipment is the equipped rows in char_items, not columns here.
CREATE TABLE IF NOT EXISTS characters (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id   INTEGER UNIQUE NOT NULL,
    name         TEXT NOT NULL,
    gender       TEXT NOT NULL DEFAULT 'M',
    gold         INTEGER NOT NULL DEFAULT 0,
    coins        INTEGER NOT NULL DEFAULT 0,
    dust         INTEGER NOT NULL DEFAULT 0,
    level        INTEGER NOT NULL DEFAULT 1,
    exp          INTEGER NOT NULL DEFAULT 0,
    class_id     INTEGER NOT NULL DEFAULT 0,
    access_level INTEGER NOT NULL DEFAULT 0,
    stat_str     INTEGER NOT NULL DEFAULT 0,
    stat_end     INTEGER NOT NULL DEFAULT 0,
    stat_dex     INTEGER NOT NULL DEFAULT 0,
    stat_int     INTEGER NOT NULL DEFAULT 0,
    stat_wis     INTEGER NOT NULL DEFAULT 0,
    stat_lck     INTEGER NOT NULL DEFAULT 0,
    upgrade_days INTEGER NOT NULL DEFAULT 0,
    upgrade_expires TEXT NOT NULL DEFAULT '2024-01-01T00:00:00',
    skin_color      INTEGER NOT NULL DEFAULT 0,
    eye_color       INTEGER NOT NULL DEFAULT 0,
    hair_color      INTEGER NOT NULL DEFAULT 0,
    trim_color      INTEGER NOT NULL DEFAULT 0,
    accessory_color INTEGER NOT NULL DEFAULT 0,
    hair_id         INTEGER NOT NULL DEFAULT 0,
    achievements    TEXT NOT NULL DEFAULT '{}',   -- per-char achievement bitfields {name:int}, e.g. ip25 (founder tiers)
    prefs           TEXT NOT NULL DEFAULT '{}',   -- per-char userPrefs (ShowHelm/ShowPet/Whisper/...) over the all-true defaults
    FOREIGN KEY (account_id) REFERENCES accounts(id)
);

-- Canonical item catalog: one row per unique item, independent of any shop.
-- raw is the item definition MINUS shop-instance fields (ShopItemID,
-- QuantityRemain); a shop listing is rebuilt by re-attaching those from shop_items.
-- Defined before char_items/shop_items because they FK-reference it (Postgres requires
-- the referenced table to already exist; SQLite tolerates either order).
-- Columns are the source of truth; the item wire dict is GENERATED from them (itemrecord).
-- The column block below is generated from itemrecord (single source of truth for the schema).
CREATE TABLE IF NOT EXISTS items (
--ITEMS_COLS--
);

-- One row per item instance a character owns (inventory AND bank). References
-- the shared items catalog - no per-instance item JSON. char_item_id mirrors the
-- wire "CharItemID"; the wire item is rebuilt from items + these instance fields.
CREATE TABLE IF NOT EXISTS char_items (
    char_item_id    INTEGER PRIMARY KEY,
    char_id         INTEGER NOT NULL,
    item_id         INTEGER UNSIGNED NOT NULL,
    quantity        INTEGER NOT NULL DEFAULT 1,
    equipped        INTEGER NOT NULL DEFAULT 0,
    banked          INTEGER NOT NULL DEFAULT 0,
    loot_id         INTEGER NOT NULL DEFAULT -1,
    char_pattern_id INTEGER,
    pattern_json    TEXT,                          -- applied gem (Pattern) JSON
    meta            TEXT NOT NULL DEFAULT '',      -- per-instance house-item metadata (cid:...)
    purchase_date   TEXT,
    FOREIGN KEY (char_id) REFERENCES characters(id),
    FOREIGN KEY (item_id) REFERENCES items(id)
);
CREATE INDEX IF NOT EXISTS idx_char_items_char ON char_items(char_id);

-- Translation between the Flash server's item IDs (SwordShine) and ours, for a shared
-- inventory across the two clients.
--
-- SwordShine's IDs are real AE item IDs, pulled from in-game shops -- except its private
-- band at 1000000+, which holds custom content that used to squat real AE IDs and was
-- renumbered out of the way.
--
-- Our catalog carries two ID spaces of its own. Below 900000 the IDs are real AE item
-- IDs. At 900000+ they are SYNTHETIC: the source API returned names and assets but no
-- IDs, so the importer derived one per asset -- item_id = 900000 + <AE asset id>, which
-- holds for every row in that band. Asset ids and item ids are different namespaces, so
-- the two catalogs cannot be translated arithmetically; this table is the bridge. It is
-- built by matching asset filename stems first and names second.
--
-- MANY-TO-ONE: several SwordShine items legitimately share one of ours, because singular
-- and dual-wield variants are distinct in AQW but share a Unity asset ("Blessed Naval
-- Cutlass" and "Dual Blessed Cutlasses" are both item 928860). So sword_item_id is unique
-- and infinity_item_id deliberately is NOT.
--
-- confidence: A_ALIGNED (ids already agree) | B_CONFIRMED (asset stem and/or name agree)
--             | C_NAME_ONLY (name agrees, asset does not -- review before trusting)
CREATE TABLE IF NOT EXISTS sword_item_map (
    sword_item_id    INTEGER UNSIGNED PRIMARY KEY,
    infinity_item_id INTEGER UNSIGNED NOT NULL,
    confidence       TEXT NOT NULL,
    basis            TEXT NOT NULL DEFAULT '',
    sword_name       TEXT NOT NULL DEFAULT '',
    FOREIGN KEY (infinity_item_id) REFERENCES items(id)
);
CREATE INDEX IF NOT EXISTS idx_sword_item_map_infinity ON sword_item_map(infinity_item_id);

CREATE TABLE IF NOT EXISTS ac_item_buybacks (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id         INTEGER UNSIGNED NOT NULL,
    char_id            INTEGER UNSIGNED NOT NULL,
    item_id            INTEGER UNSIGNED NOT NULL,
    remaining_quantity INTEGER NOT NULL DEFAULT 1,
    unit_price         INTEGER NOT NULL,
    sold_at            REAL NOT NULL,
    FOREIGN KEY (account_id) REFERENCES accounts(id),
    FOREIGN KEY (char_id) REFERENCES characters(id),
    FOREIGN KEY (item_id) REFERENCES items(id)
);
CREATE INDEX IF NOT EXISTS idx_ac_buybacks_account ON ac_item_buybacks(account_id, sold_at);

-- Audit each account-manager exchange. The token is itself an owned char_items
-- row; redemption consumes one token quantity and grants one selected item.
CREATE TABLE IF NOT EXISTS token_redemptions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id    INTEGER UNSIGNED NOT NULL,
    char_id       INTEGER UNSIGNED NOT NULL,
    token_item_id INTEGER UNSIGNED NOT NULL,
    item_id       INTEGER UNSIGNED NOT NULL,
    redeemed      REAL NOT NULL,
    FOREIGN KEY (account_id) REFERENCES accounts(id),
    FOREIGN KEY (char_id) REFERENCES characters(id),
    FOREIGN KEY (token_item_id) REFERENCES items(id),
    FOREIGN KEY (item_id) REFERENCES items(id)
);

-- Player sponsorships of a catalog item (the AE Kickstarter "Benevolent Founder" credit:
-- a founder spends a Sponsorship token to have their name added to an item's credits). The
-- client's item-info panel (ItemPreviewNew) requests these via getItemSponsors and shows a
-- Sponsors button/list when non-empty. Many players may sponsor one item; ordered by seq.
-- has_house = whether that sponsor has a visitable house on THIS server (drives the client's
-- "visit home" button); 0 for off-server credits (e.g. the original AE sponsor).
CREATE TABLE IF NOT EXISTS item_sponsors (
    item_id      INTEGER UNSIGNED NOT NULL,
    player_name  TEXT NOT NULL,
    has_house    INTEGER NOT NULL DEFAULT 1,
    seq          INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (item_id, player_name),
    FOREIGN KEY (item_id) REFERENCES items(id)
);
CREATE INDEX IF NOT EXISTS idx_item_sponsors_item ON item_sponsors(item_id);

-- Redeem codes (client RedeemCodeModal: redeem_code / redeem_history). A code grants one or
-- more rewards atomically to an account, once per account, capped by max_uses. Each reward is
-- an item grant or an achievement-bitfield bit (reward_field/reward_value); redeem_code_uses is
-- the per-account ledger that enforces single-use. Schema was live on prod before it was ever
-- in git — added here so a fresh seed is self-contained (CREATE IF NOT EXISTS = no-op on prod).
CREATE TABLE IF NOT EXISTS redeem_codes (
    code         TEXT NOT NULL PRIMARY KEY,
    description  TEXT NOT NULL DEFAULT '',
    max_uses     INTEGER NOT NULL DEFAULT 0,
    active       INTEGER NOT NULL DEFAULT 1,
    created      REAL
);
CREATE TABLE IF NOT EXISTS redeem_code_rewards (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    code         TEXT NOT NULL,
    reward_type  TEXT NOT NULL,
    reward_value INTEGER NOT NULL,
    reward_qty   INTEGER NOT NULL DEFAULT 1,
    reward_field TEXT NOT NULL DEFAULT 'ip25'
);
CREATE TABLE IF NOT EXISTS redeem_code_uses (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id   INTEGER UNSIGNED NOT NULL,
    code         TEXT NOT NULL,
    description  TEXT NOT NULL DEFAULT '',
    redeemed_at  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_redeem_rewards_code ON redeem_code_rewards(code);
CREATE INDEX IF NOT EXISTS idx_redeem_uses_account ON redeem_code_uses(account_id);
-- Separate unique claims preserve historical duplicate ledger rows without deleting audit data.
CREATE TABLE IF NOT EXISTS redeem_code_claims (
    account_id INTEGER UNSIGNED NOT NULL,
    code TEXT NOT NULL,
    PRIMARY KEY (account_id, code)
);
CREATE TABLE IF NOT EXISTS revoked_browser_sessions (
    token_hash TEXT PRIMARY KEY,
    expires REAL NOT NULL
);

-- Privacy-preserving economy/support ledger. Internal ids and game usernames are enough for
-- support investigations; deliberately do not store IP addresses, email, hardware or device ids.
CREATE TABLE IF NOT EXISTS account_audit (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id  INTEGER UNSIGNED NOT NULL,
    char_id     INTEGER UNSIGNED,
    actor_type  TEXT NOT NULL,
    actor_name  TEXT NOT NULL DEFAULT '',
    action      TEXT NOT NULL,
    item_id     INTEGER UNSIGNED,
    quantity    INTEGER,
    currency    TEXT,
    amount      INTEGER,
    detail      TEXT NOT NULL DEFAULT '',
    created     REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_account_audit_account ON account_audit(account_id, created);
CREATE INDEX IF NOT EXISTS idx_account_audit_action ON account_audit(action, created);

-- Generated hero statues are ordinary floor items whose rendered appearance is a
-- server-side snapshot. DynamicStatue carries cid:<char_id> in houseItem.Meta;
-- the image endpoint resolves that stable id through this table.
CREATE TABLE IF NOT EXISTS statues (
    char_id      INTEGER UNSIGNED PRIMARY KEY,
    item_id      INTEGER UNSIGNED NOT NULL,
    generated_at REAL NOT NULL,
    snapshot     TEXT NOT NULL,
    image        BLOB,
    FOREIGN KEY (char_id) REFERENCES characters(id),
    FOREIGN KEY (item_id) REFERENCES items(id)
);

-- Loose gem inventory (the enhancement "gem bag" — initPlayer.patterns[]). A gem the player
-- owns but hasn't slotted lives here as its rolled ItemPattern JSON (rarity=Quality, the
-- archetype's primary stat, target EquipSpot). equipPattern moves one onto a gear item
-- (char_items.pattern_json); removePattern returns it here. Class-based rarity gems (2026-07-02).
CREATE TABLE IF NOT EXISTS char_patterns (
    char_pattern_id INTEGER PRIMARY KEY,
    char_id         INTEGER UNSIGNED NOT NULL,
    pattern_json    TEXT NOT NULL,                 -- the loose gem (ItemPattern) JSON
    FOREIGN KEY (char_id) REFERENCES characters(id)
);
CREATE INDEX IF NOT EXISTS idx_char_patterns_char ON char_patterns(char_id);

-- Shop meta (loadShop wrapper minus items). Columns are the source of truth; the loadShop
-- blob is GENERATED from them (shoprecord). The column block below is generated from shoprecord.
CREATE TABLE IF NOT EXISTS shops (
--SHOPS_COLS--
);

-- A shop's offer of an item: references items(item_id) and carries only the
-- shop-instance fields (price, currency, stock). No embedded item JSON.
CREATE TABLE IF NOT EXISTS shop_items (
    shop_id         INTEGER NOT NULL,
    shop_item_id    INTEGER NOT NULL,
    item_id         INTEGER UNSIGNED NOT NULL,
    cost            INTEGER NOT NULL DEFAULT 0,
    coins           INTEGER NOT NULL DEFAULT 0,
    quantity_remain INTEGER NOT NULL DEFAULT -1,
    PRIMARY KEY (shop_id, shop_item_id),
    FOREIGN KEY (item_id) REFERENCES items(id)
);

-- ===================== CATALOG (shared content) ========================
-- Mirrors AQ2D_Server.Game_Engine.*: the "what exists" content the world is
-- built from. Per-character state and the authored pad layer reference these.

-- Monster/NPC catalog (MonID -> definition + art Bundle). Replaces the old
-- in-memory montemplates catalog as the source of truth; pad_npcs.mon_id -> here.
-- Canonical monster/NPC record. Columns are the source of truth; the two wire shapes
-- (spawn monBranch + GetMonsterData catalog) are GENERATED from them via monrecord, so they
-- can't drift. The column block below is generated from monrecord (single source of truth).
CREATE TABLE IF NOT EXISTS monsters (
--MONSTERS_COLS--
);

-- Per-monster drop table: the catalog items a monster can drop, each with its own INDEPENDENT
-- per-kill rate. Authored server content (seeded from data/monster_drops.json). A monster with no
-- rows here drops nothing — there is no global fallback pool.
CREATE TABLE IF NOT EXISTS monster_drops (
    mon_id   INTEGER NOT NULL,
    item_id  INTEGER UNSIGNED NOT NULL,
    rate     REAL    NOT NULL DEFAULT 0.1,   -- 0..1 independent chance this item drops on a kill
    quantity INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (mon_id, item_id),
    FOREIGN KEY (item_id) REFERENCES items(id)
);

-- Global drop table: items EVERY monster can drop, ON TOP OF its own monster_drops (e.g. gems
-- that drop universally). Each row rolls INDEPENDENTLY per kill, same model as monster_drops.
-- INTENTIONAL additive pool (seeded from data/global_drops.json) — NOT the removed band-aid
-- fallback that masked monsters lacking an authored table.
CREATE TABLE IF NOT EXISTS global_drops (
    item_id  INTEGER UNSIGNED NOT NULL PRIMARY KEY,
    rate     REAL    NOT NULL DEFAULT 0.05,
    quantity INTEGER NOT NULL DEFAULT 1,
    FOREIGN KEY (item_id) REFERENCES items(id)
);

-- Map/Area catalog (the AreaJoin metadata). Cell geometry stays in the .unity3d
-- bundle; monBranch is computed from the pad layer, not stored here.
CREATE TABLE IF NOT EXISTS maps (
    map_id        INTEGER PRIMARY KEY,
    area_name     TEXT,
    str_map_name  TEXT,
    display_name  TEXT,
    prefab_name   TEXT,
    soundtrack_id INTEGER,
    int_type      INTEGER,
    bundle        TEXT,        -- map AssetBundleData (JSON)
    quest_ids     TEXT,        -- JSON array
    raw           TEXT NOT NULL,  -- AreaJoin metadata (area minus monBranch/uoBranch)
    doc           TEXT            -- full served map doc {"area":...,"cells":...} (R2 removed)
);

-- Quest catalog (AQ2D Quest) + its polymorphic turnins and rewards.
CREATE TABLE IF NOT EXISTS quests (
    quest_id          INTEGER PRIMARY KEY,
    name              TEXT,
    descr             TEXT,
    end_text          TEXT,
    faction_id        INTEGER,
    class_name        TEXT,
    prev_quest        INTEGER,
    map_id            INTEGER,
    dialog_id         INTEGER,
    apop_id           INTEGER,
    turnin_type       INTEGER,
    notification_type INTEGER,
    reward_count      INTEGER,
    turnin_map_id     INTEGER,
    turnin_npc_id     INTEGER,
    turnin_frame      TEXT,
    turnin_pad        TEXT,
    raw               TEXT NOT NULL
);

-- One row per quest objective (the polymorphic `turnin` entries). Columns are the source of
-- truth; the served `turnin` array is GENERATED from them (qturninrecord). Column block below
-- is generated from qturninrecord (single source of truth for the objective schema).
CREATE TABLE IF NOT EXISTS quest_turnins (
--QUEST_TURNINS_COLS--
);

-- Authored objective -> monster mapping for kill-credit: which monster catalog ids credit a
-- given quest objective. Explicit and self-describing (quest_id, qoid, mon_id), so kill crediting
-- is a table lookup instead of guessing by RefIDs/name. One row per (objective, monster).
CREATE TABLE IF NOT EXISTS quest_objective_refs (
    quest_id INTEGER NOT NULL,
    qoid     INTEGER NOT NULL,
    mon_id   INTEGER NOT NULL,
    PRIMARY KEY (qoid, mon_id)
);

-- Probabilistic drop roll per quest objective: on a credited kill, the objective drops with
-- `chance` probability, granting a random `min_qty`..`max_qty` (so a kill can drop all/some/none
-- of a quest's objectives, in varying amounts). Absent row = deterministic +1 (chance 1, 1..1).
CREATE TABLE IF NOT EXISTS quest_objective_drops (
    qoid    INTEGER PRIMARY KEY,
    chance  DOUBLE PRECISION NOT NULL DEFAULT 1.0,
    min_qty INTEGER NOT NULL DEFAULT 1,
    max_qty INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_quest_turnins_qoid ON quest_turnins(qo_id);

CREATE TABLE IF NOT EXISTS quest_rewards (
    quest_id INTEGER NOT NULL,
    idx      INTEGER NOT NULL,
    kind     TEXT,                -- QuestRewardType: Static / Roll / Choose / Random
    item_id  INTEGER UNSIGNED,
    quantity INTEGER,
    rate     REAL    NOT NULL DEFAULT 0,   -- QuestRewardItem.Rate (drop chance for Roll/Random)
    hidden   INTEGER NOT NULL DEFAULT 0,   -- QuestRewardItem.Hidden
    PRIMARY KEY (quest_id, idx),
    FOREIGN KEY (quest_id) REFERENCES quests(quest_id) ON DELETE CASCADE
);

-- Apop catalog (AQ2D Apop: NPC dialog/menu/cutscene trees). raw is the apop
-- object the client expects as a JSON *string* in getApop's apopData[id].
-- A pad NPC's apop_id points here; getApop serves these by id.
CREATE TABLE IF NOT EXISTS apops (
    apop_id INTEGER PRIMARY KEY,
    name    TEXT,
    raw     TEXT NOT NULL
);

-- Dialogger cutscenes (the in-client dialog/cutscene editor's Dialogger_Data).
-- Saved/loaded via tweak/DialoggerSave/Load; apop OpenCutscene buttons ref these by id.
CREATE TABLE IF NOT EXISTS cutscenes (
    id  INTEGER PRIMARY KEY,
    raw TEXT NOT NULL
);

-- Skill Forge: classes, their slotted skills, and the shared skill library.
-- Authored in-client via the node editor (sfInit loads, sfNew/sfSave/... persist),
-- served back the same self-hosted way as apops/cutscenes. Modelled on AE's
-- Class/Skill entities: a Skill's node-graph (Data) and editor layout (ForgeData)
-- are irreducible value-objects, so they stay as scoped JSON columns (the
-- RAW-COLUMN principle); everything queried/edited is a real column.
CREATE TABLE IF NOT EXISTS classes (
    class_id INTEGER PRIMARY KEY,
    name     TEXT    NOT NULL,
    bundle   TEXT,                               -- class armor .unity3d
    rig      TEXT,                               -- full eqp.Class entry (skin Bundle +
                                                 -- ClassParticleBundle) = the avatar's
                                                 -- authoritative class visual/particle rig
    resource TEXT,                               -- updateClass bar model (ResponseClass):
                                                 -- {model, ResourceColor, MaxRP, Threshold,
                                                 -- ThresholdColor} - DS=determination,
                                                 -- others=mana (per capture)
    raw      TEXT                                -- optional extra class meta
);

-- A skill occupying a slot on a class. The same skill_id can sit on many
-- classes (sfLink shares; sfClone copies into a new id) - hence a join table.
CREATE TABLE IF NOT EXISTS class_skills (
    class_id INTEGER NOT NULL,
    slot     INTEGER NOT NULL,                   -- 0-based slot (0 = auto-attack)
    skill_id INTEGER NOT NULL,
    PRIMARY KEY (class_id, slot),
    FOREIGN KEY (class_id) REFERENCES classes(class_id) ON DELETE CASCADE
);

-- The shared skill library (CharacterClass.AllSkills). data/forge_data are the
-- node-graph JArrays the in-client editor exports/loads; the rest are columns.
CREATE TABLE IF NOT EXISTS skills (
    skill_id     INTEGER PRIMARY KEY,
    action       INTEGER NOT NULL DEFAULT 0,     -- Skill.ActionType
    name         TEXT,
    description  TEXT,
    icon         TEXT,
    slot         INTEGER NOT NULL DEFAULT 0,
    data         TEXT    NOT NULL DEFAULT '[{},{}]',   -- node logic graph (JArray)
    forge_data   TEXT    NOT NULL DEFAULT '[{},{}]',   -- editor layout (JArray)
    auto_h_range REAL    NOT NULL DEFAULT 0,
    auto_v_range REAL    NOT NULL DEFAULT 0,
    auto_hold_at_range INTEGER NOT NULL DEFAULT 0,
    mana         INTEGER NOT NULL DEFAULT 0
);

-- Live AE's asset-bundle catalog (data/GetAssetBundlesByIDs), harvested by
-- capture/harvest_catalog.py against the CONTENT/STAGE/LIVE hosts. This is upstream
-- Artix metadata, not player data - it maps a bundle ID to its CDN filename and per-
-- environment version, letting us spot content (e.g. unshipped classes/animations)
-- that's built on content/stage but never pushed live (version_live=0).
CREATE TABLE IF NOT EXISTS asset_bundles (
    bundle_id       INTEGER PRIMARY KEY,
    name            TEXT,
    type            TEXT,                    -- CLASS / ARMOR / ITEM / NPC / MAP / CUTSCENE / ...
    filename        TEXT,
    version_content INTEGER NOT NULL DEFAULT 0,
    version_stage   INTEGER NOT NULL DEFAULT 0,
    version_live    INTEGER NOT NULL DEFAULT 0,
    dependency_id   INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_asset_bundles_type ON asset_bundles(type);

-- Hair catalog (Data/GetBaseClasses .hairs — the /charedit + HairShop picker list). Was a JSON
-- blob under kv['base_classes'] (only the 24 default-class hairs AE's base-classes response
-- carries); promoted to a real table, same pattern as monrecord/itemrecord, so the full harvested
-- hairstyle roster (capture/harvest_bundle catalog, Type=="HAIR") can live here instead.
-- SHARED with SwordShine, like `items`: its hair rows are real AE captures, so the two
-- catalogues are the same catalogue seen twice (16 of the 24 hairs we hold at a real id sit
-- at the SAME id there). Columns are SwordShine's, because it owns the table on MariaDB.
-- `File` is AQW's Flash asset and `unity_file`/`bundle` are ours: different files for the
-- same hair, so both ride on the one row. The Unity client renders from `bundle`.
CREATE TABLE IF NOT EXISTS hairs (
    id         INTEGER PRIMARY KEY,
    Gender     TEXT,          -- "M" / "F"
    Name       TEXT,
    File       TEXT,          -- AQW:   hair/<Gender>/<Name>.swf
    unity_file TEXT,          -- AQW:I: hair/<Gender>/<assetid>_<Name>_<Gender>.unity3d
    bundle     TEXT           -- AQW:I: {ID,Name,Filename,VersionStage,VersionLive} JSON
);
CREATE INDEX IF NOT EXISTS idx_hairs_gender ON hairs(Gender);

-- Soundtrack/audio bundles (harvest catalog Type MUSIC/AUDIO). The client streams these by
-- bundle id (data/getsoundtracks); this is the catalog of what exists.
CREATE TABLE IF NOT EXISTS soundtracks (
    bundle_id     INTEGER PRIMARY KEY,
    name          TEXT,
    kind          TEXT,          -- MUSIC / AUDIO
    filename      TEXT,
    version_stage INTEGER NOT NULL DEFAULT 0,
    version_live  INTEGER NOT NULL DEFAULT 0
);

-- Cinematic/cutscene ART bundles (harvest catalog Type CUTSCENE). Distinct from the `cutscenes`
-- table above, which stores authored Dialogger dialog JSON — these are the .unity3d scene assets.
CREATE TABLE IF NOT EXISTS cutscene_bundles (
    bundle_id     INTEGER PRIMARY KEY,
    name          TEXT,
    filename      TEXT,
    version_stage INTEGER NOT NULL DEFAULT 0,
    version_live  INTEGER NOT NULL DEFAULT 0
);

-- Misc key/value (e.g. the next CharItemID counter).
CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT);

-- Per-character quest PROGRESS (the quest catalog lives in `quests`/`quest_turnins`;
-- these track what each character has accepted/completed and per-objective counters).
CREATE TABLE IF NOT EXISTS char_quests (
    char_id  INTEGER UNSIGNED NOT NULL,
    quest_id INTEGER NOT NULL,
    status   INTEGER NOT NULL DEFAULT 1,     -- 1=accepted, 2=completed
    PRIMARY KEY (char_id, quest_id)
);
CREATE TABLE IF NOT EXISTS char_quest_objectives (
    char_id  INTEGER UNSIGNED NOT NULL,
    qoid     INTEGER NOT NULL,               -- objective id (Turnin.QOID)
    quantity INTEGER NOT NULL DEFAULT 0,     -- progress toward the objective's required Quantity
    PRIMARY KEY (char_id, qoid)
);

-- Per-character saved house layouts (housesave: [houseMapID, frame, itemsJSON]).
CREATE TABLE IF NOT EXISTS char_houses (
    char_id      INTEGER UNSIGNED NOT NULL,
    house_map_id INTEGER NOT NULL,
    frame        TEXT,
    data         TEXT,                        -- JSON array of placed house items
    PRIMARY KEY (char_id, house_map_id)
);

-- Authored NPC/monster placement ("pad") layer. When a map is "taken over"
-- (map_state.authored=1) the server serves monBranch compiled from these pads
-- instead of the captured one, so edits (remove/add) persist.

-- A placement slot ("pad") on a map: where it is and which frame/cell it's in.
-- Mirrors the editor's PadData (ID,X,Y,MapID,Frame,Direction,RequirementData).
CREATE TABLE IF NOT EXISTS map_pads (
    map              TEXT    NOT NULL,
    pad_id           INTEGER NOT NULL,
    x                REAL    NOT NULL DEFAULT 0,
    y                REAL    NOT NULL DEFAULT 0,
    area_id          INTEGER NOT NULL DEFAULT 0,   -- PadData.MapID
    frame            TEXT    NOT NULL DEFAULT 'Enter',
    direction        INTEGER NOT NULL DEFAULT 1,
    requirement_data TEXT,
    PRIMARY KEY (map, pad_id)
);

-- The NPC(s)/monster(s) on a pad - one row each, every editor NPCEditData field
-- a column. mon_id is the catalog MonID (art/behaviour come from that template);
-- the rest are this placement's editable overrides.
CREATE TABLE IF NOT EXISTS pad_npcs (
    map              TEXT    NOT NULL,
    pad_id           INTEGER NOT NULL,
    slot             INTEGER NOT NULL,            -- order on the pad (0,1,2,...)
    mon_id           INTEGER NOT NULL,
    name             TEXT,
    level            INTEGER NOT NULL DEFAULT 1,
    apop_id          INTEGER NOT NULL DEFAULT -1,
    subtitle         TEXT,
    max_hp           INTEGER NOT NULL DEFAULT 100,
    scale_min        REAL    NOT NULL DEFAULT 1.0,
    scale_max        REAL    NOT NULL DEFAULT 1.0,
    gold             INTEGER NOT NULL DEFAULT 0,
    exp              INTEGER NOT NULL DEFAULT 0,
    rep              INTEGER NOT NULL DEFAULT 0,
    no_turn          INTEGER NOT NULL DEFAULT 0,
    no_move          INTEGER NOT NULL DEFAULT 0,
    unkillable       INTEGER NOT NULL DEFAULT 0,
    death_at_percent INTEGER NOT NULL DEFAULT 0,
    boss             INTEGER NOT NULL DEFAULT 0,
    element          INTEGER NOT NULL DEFAULT 0,
    race             INTEGER NOT NULL DEFAULT 0,
    agro             INTEGER NOT NULL DEFAULT 0,
    class_id         INTEGER NOT NULL DEFAULT 0,
    skin_color       INTEGER NOT NULL DEFAULT 0,
    hair_color       INTEGER NOT NULL DEFAULT 0,
    eye_color        INTEGER NOT NULL DEFAULT 0,
    base_color       INTEGER NOT NULL DEFAULT 0,
    trim_color       INTEGER NOT NULL DEFAULT 0,
    accessory_color  INTEGER NOT NULL DEFAULT 0,
    hair_id          INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (map, pad_id, slot),
    FOREIGN KEY (map, pad_id) REFERENCES map_pads(map, pad_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_pad_npcs ON pad_npcs(map, pad_id);

CREATE TABLE IF NOT EXISTS map_state (
    map         TEXT PRIMARY KEY,
    authored    INTEGER NOT NULL DEFAULT 0,
    next_pad_id INTEGER NOT NULL DEFAULT 9000,  -- new pads start high to avoid
    area_id     INTEGER NOT NULL DEFAULT 0      -- colliding with captured MonMapIDs
);

-- Mutual friendships (two rows per pair). friend_id is the other character's id;
-- the client's FriendObject is {Level,ID,Name,Server} rebuilt from that char row.
CREATE TABLE IF NOT EXISTS friends (
    char_id   INTEGER UNSIGNED NOT NULL,
    friend_id INTEGER UNSIGNED NOT NULL,     -- also a character id, despite the name
    PRIMARY KEY (char_id, friend_id)
);

-- Guilds. One row per guild; members carry a guild_id + rank on the character row
-- (added below via ensure-column). ip25/founder is unrelated. [[name-plates]]
CREATE TABLE IF NOT EXISTS guilds (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    name      TEXT NOT NULL UNIQUE,
    motd      TEXT NOT NULL DEFAULT '',
    owner     INTEGER NOT NULL DEFAULT 0,        -- owning character id
    hall_map  TEXT NOT NULL DEFAULT '',          -- guild hall map ('' = default), like a house map
    hall_data TEXT NOT NULL DEFAULT '',          -- JSON {frame:[PlacedHouseItem]} the leader decorates
    tag_color TEXT NOT NULL DEFAULT ''           -- guild-default nameplate tag colour ('' = base green)
);
"""


# =========================== dialect layer (sqlite | postgres) ==============
# The whole codebase talks to a `sqlite3.Connection`-shaped object: conn.execute(sql, params)
# with `?` placeholders, returning a cursor whose rows are indexable by BOTH name and position.
# On Postgres we wrap psycopg in the same shape so the ~219 call sites stay unchanged: the
# wrapper rewrites `?`->`%s` and yields rows that quack like sqlite3.Row.

def _to_pyformat(sql):
    """Rewrite SQLite `?` placeholders to psycopg `%s`, leaving `?` inside '...' string
    literals alone, and escape any literal `%` to `%%` (psycopg scans the whole query)."""
    out, in_str = [], False
    for ch in sql:
        if ch == "'":
            in_str = not in_str
            out.append(ch)
        elif ch == "%":
            out.append("%%")
        elif ch == "?" and not in_str:
            out.append("%s")
        else:
            out.append(ch)
    return "".join(out)


def _split_statements(script):
    """Split a multi-statement SQL script into individual statements, treating ';' inside
    '...' string literals or `-- ...` line comments as ordinary text. Yields non-blank,
    non-comment-only statements (each fed to psycopg one at a time)."""
    stmts, buf, in_str, in_comment = [], [], False, False
    i, n = 0, len(script)
    while i < n:
        ch = script[i]
        if in_comment:
            buf.append(ch)
            if ch == "\n":
                in_comment = False
        elif in_str:
            buf.append(ch)
            if ch == "'":
                in_str = False
        elif ch == "'":
            in_str = True
            buf.append(ch)
        elif ch == "-" and i + 1 < n and script[i + 1] == "-":
            in_comment = True
            buf.append(ch)
        elif ch == ";":
            stmts.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
        i += 1
    stmts.append("".join(buf))
    # keep only statements that contain actual SQL (strip blank/comment-only tails)
    out = []
    for s in stmts:
        body = "\n".join(ln for ln in s.splitlines() if not ln.strip().startswith("--"))
        if body.strip():
            out.append(s)
    return out


class _PgRow(Mapping):
    """A query row that mimics sqlite3.Row: indexable by column name ("col") AND by
    position ([0]), exposes .keys(), and unpacks as a mapping ({**row})."""
    __slots__ = ("_cols", "_idx", "_vals")

    def __init__(self, cols, idx, vals):
        self._cols, self._idx, self._vals = cols, idx, vals

    def __getitem__(self, key):
        if isinstance(key, int):
            return self._vals[key]
        return self._vals[self._idx[key]]

    def __iter__(self):
        return iter(self._cols)            # Mapping iterates keys (so {**row} works)

    def __len__(self):
        return len(self._cols)

    def keys(self):
        return self._cols


def _adapt_row(cols, vals):
    """Convert SwordShine's encodings back to the engine's, in place.

    Only the MariaDB path calls this, and only two facts differ: a colour is stored as
    char(6) hex where we hold a 24-bit int, and account timestamps are DATETIME where we
    hold an epoch. Both conversions are lossless, so ONE column serves both games rather
    than two columns drifting apart.

    Dispatching on the value's TYPE rather than the column's table is what makes this safe
    to apply to every row from every query: monsters.skin_color is already an int and
    redeem_codes.created is already a float, so neither matches and neither is altered.
    That is also what makes it complete -- no reader of a character row can be missed.
    """
    out = None
    for i, c in enumerate(cols):
        v = vals[i]
        if v is None:
            continue
        if c in _MYSQL_COLOR_COLS and isinstance(v, str):
            try:
                v = int(v, 16)
            except ValueError:
                continue
        elif c in _MYSQL_EPOCH_COLS and isinstance(v, (datetime.datetime, datetime.date)):
            v = _epoch(v)
        elif c in _MYSQL_ISO_COLS and isinstance(v, (datetime.datetime, datetime.date)):
            # Without this membership_days() gets a datetime where it expects a string,
            # fromisoformat raises, the except returns 0 -- and every member silently
            # loses their membership.
            v = v.strftime(_MYSQL_ISO_FMT)
        else:
            continue
        if out is None:
            out = list(vals)
        out[i] = v
    return vals if out is None else out


def _epoch(d):
    """A date or datetime as a UTC epoch float, matching what the engine stores elsewhere."""
    if not isinstance(d, datetime.datetime):
        d = datetime.datetime(d.year, d.month, d.day)
    return d.replace(tzinfo=datetime.timezone.utc).timestamp()


def _pg_row_factory(cursor):
    cols = [c.name for c in cursor.description] if cursor.description else []
    idx = {c: i for i, c in enumerate(cols)}
    return lambda vals: _PgRow(cols, idx, vals)


class _PgConnection:
    """A thin sqlite3.Connection-shaped wrapper over a psycopg connection. Provides
    conn.execute/.executemany/.executescript/.commit/.close and a `with` that commits on a
    clean exit but does NOT close (sqlite3 semantics - the server holds a long-lived conn)."""
    def __init__(self, raw):
        self._raw = raw

    def execute(self, sql, params=()):
        cur = self._raw.cursor(row_factory=_pg_row_factory)
        if params:
            cur.execute(_to_pyformat(sql), tuple(params))
        else:
            cur.execute(sql)               # no params -> sent verbatim (no %/placeholder handling)
        return cur

    def executemany(self, sql, seq):
        cur = self._raw.cursor(row_factory=_pg_row_factory)
        cur.executemany(_to_pyformat(sql), [tuple(p) for p in seq])
        return cur

    def executescript(self, script):
        # psycopg executes one statement per call; split the multi-statement SCHEMA on ';',
        # ignoring ';' inside '...' string literals and inside `-- ...` line comments (the
        # schema's comments contain semicolons, so a plain split would mangle them).
        cur = self._raw.cursor()
        for stmt in _split_statements(script):
            cur.execute(stmt)
        return cur

    def commit(self):
        self._raw.commit()

    def rollback(self):
        self._raw.rollback()

    def close(self):
        self._raw.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc_type is None:
            self._raw.commit()             # commit on success; leave the connection open
        else:
            self._raw.rollback()
        return False


# =========================== MariaDB / MySQL dialect ========================
# Same contract as the Postgres wrapper: a sqlite3.Connection-shaped object taking `?`
# placeholders, so the ~219 call sites stay unchanged. MySQL uses `%s` like psycopg, so
# _to_pyformat is reused verbatim; only three SQL constructs actually differ.

# Nine of our table names also exist in SwordShine's schema with entirely different
# shapes -- its `quests` has 26 columns and no quest_id at all. Sharing one database means
# ours are prefixed so the two cannot collide. `items` is deliberately ABSENT: that one IS
# shared, and is SwordShine's table.
#
# The rename happens here rather than across ~185 call sites: every statement already
# passes through this translator, so one map covers DDL, DML and foreign keys alike, and
# no call site can be missed. Only the MySQL path is affected; SQLite and Postgres keep
# the original names.
_MYSQL_TABLE_PREFIX = (
    "sword_item_map", "ac_item_buybacks", "token_redemptions", "item_sponsors",
    "redeem_codes", "redeem_code_rewards", "redeem_code_uses", "redeem_code_claims",
    "revoked_browser_sessions", "account_audit", "statues", "char_patterns",
    "shops", "shop_items", "monsters", "monster_drops", "global_drops", "maps",
    "quests", "quest_turnins", "quest_objective_refs", "quest_objective_drops",
    "quest_rewards", "apops", "cutscenes", "classes", "class_skills", "skills",
    "asset_bundles", "soundtracks", "cutscene_bundles", "kv", "char_quests",
    "char_quest_objectives", "char_houses", "map_pads", "pad_npcs", "map_state",
)

# These logical names are compatibility views onto the shared SwordShine-owned rows.
# Their physical `shared_` names make ownership unmistakable in MariaDB.
_MYSQL_SHARED_NAMES = {
    "accounts": "shared_accounts", "characters": "shared_characters",
    "char_items": "shared_char_items", "friends": "shared_friends",
    "guilds": "shared_guilds",
}

# On MariaDB these three are NOT ours: they are VIEWS over SwordShine's `users` and
# `users_items`, so a player's account, character and bag are ONE row set that both games
# read and write. The engine's ~341 queries against them are left exactly as written --
# rewriting each one would be 341 chances to miss a place a player's gold or inventory is
# saved. What a view cannot express is handled here instead: SwordShine stores colours as
# char(6) hex and timestamps as DATETIME where we use an int and an epoch, so _adapt_row()
# converts on the way out and _MYSQL_WRITE_CAST on the way in.
_MYSQL_SHARED_LOGICAL = tuple(_MYSQL_SHARED_NAMES)
_MYSQL_SHARED_VIEWS = tuple(_MYSQL_SHARED_NAMES.values())

# Reads: column name -> how to turn SwordShine's stored value into ours. Each is guarded by
# the value's TYPE, so a same-named column on another table (monsters.skin_color is already
# an int; redeem_codes.created is already an epoch) passes through untouched.
_MYSQL_COLOR_COLS = ("skin_color", "eye_color", "hair_color", "trim_color", "accessory_color")
_MYSQL_EPOCH_COLS = ("created", "last_accessed")
# Held as an ISO-8601 string with a `T` separator, which is what the engine writes
# (set_membership) and what it parses back (datetime.fromisoformat in membership_days) and
# what it hands the client verbatim. SwordShine stores the same instant as a DATETIME.
_MYSQL_ISO_COLS = ("upgrade_expires", "purchase_date")
_MYSQL_ISO_FMT = "%Y-%m-%dT%H:%M:%S"

# Writes: the same two conversions as SQL, applied to the bound parameter. Scoped to
# statements whose target is one of the views above, so an UPDATE of monsters.skin_color
# (a real int column) is never touched.
_MYSQL_WRITE_CAST = {c: "LPAD(HEX(%s),6,'0')" for c in _MYSQL_COLOR_COLS}
_MYSQL_WRITE_CAST.update({c: "FROM_UNIXTIME(%s)" for c in _MYSQL_EPOCH_COLS})
_MYSQL_WRITE_CAST.update({c: "REPLACE(%s,'T',' ')" for c in _MYSQL_ISO_COLS})
_MYSQL_PHYSICAL = {name: "inf_" + name for name in _MYSQL_TABLE_PREFIX}
_MYSQL_PHYSICAL.update(_MYSQL_SHARED_NAMES)
_MYSQL_RENAME = re.compile(
    r"\b(FROM|JOIN|INTO|UPDATE|TABLE|REFERENCES|EXISTS|ON)\s+(%s)\b"
    % "|".join(sorted(_MYSQL_PHYSICAL, key=len, reverse=True)), re.I)
_MYSQL_QUALIFIED = re.compile(
    r"\b(%s)\." % "|".join(sorted(_MYSQL_PHYSICAL, key=len, reverse=True)), re.I)


def _to_mysql(sql):
    """Rewrite the SQLite/Postgres constructs MariaDB does not accept.

      ON CONFLICT(c) DO NOTHING       -> ON DUPLICATE KEY UPDATE c=c   (a no-op write)
      ON CONFLICT(c) DO UPDATE SET x  -> ON DUPLICATE KEY UPDATE x
      CAST(x AS BIGINT|INTEGER)       -> CAST(x AS SIGNED)
      CAST(x AS TEXT)                 -> CAST(x AS CHAR)
      excluded.col                    -> VALUES(col)   (the proposed row, in an upsert)
    """
    # The no-op assignment must be QUALIFIED with the insert target: in an
    # `INSERT INTO t (...) SELECT ... JOIN ...`, a bare column name is ambiguous between
    # the target and the selected tables ("Column 'account_id' in UPDATE is ambiguous").
    target = re.search(r"INSERT\s+(?:IGNORE\s+)?INTO\s+[\"`]?(\w+)", sql, re.I)
    # Through _phys(), because the qualified name is emitted BEFORE _MYSQL_RENAME runs below
    # and the rename only matches a name that follows INTO/FROM/JOIN -- so an unqualified
    # `hairs`.`hair_id` would survive into the ON DUPLICATE clause and point at SwordShine's
    # table ("Unknown column 'hairs.hair_id'").
    tname = _phys(target.group(1)) if target else None

    def _do_nothing(m):
        first = m.group(1).split(",")[0].strip().strip('"').strip("`")
        col = "`%s`.`%s`" % (tname, first) if tname else "`%s`" % first
        return "ON DUPLICATE KEY UPDATE %s=%s" % (col, col)

    sql = re.sub(r"ON\s+CONFLICT\s*\(([^)]*)\)\s*DO\s+NOTHING", _do_nothing, sql,
                 flags=re.I)
    sql = re.sub(r"ON\s+CONFLICT\s*\(([^)]*)\)\s*DO\s+UPDATE\s+SET",
                 "ON DUPLICATE KEY UPDATE", sql, flags=re.I)
    sql = re.sub(r"CAST\s*\((.*?)\s+AS\s+(?:BIGINT|INTEGER)\s*\)", r"CAST(\1 AS SIGNED)",
                 sql, flags=re.I)
    sql = re.sub(r"CAST\s*\((.*?)\s+AS\s+TEXT\s*\)", r"CAST(\1 AS CHAR)", sql, flags=re.I)
    # Postgres names the would-be-inserted row `excluded`; MySQL exposes it as VALUES(col).
    # The column is re-quoted because SwordShine's names include MySQL reserved words --
    # `Range` most notably -- which are a syntax error bare inside VALUES().
    sql = re.sub(r"\bexcluded\.\"?(\w+)\"?", r'VALUES("\1")', sql, flags=re.I)
    # SQLite's REPLACE spelling; MySQL's is REPLACE INTO with the same delete-then-insert
    # semantics.
    sql = re.sub(r"\bINSERT\s+OR\s+REPLACE\s+INTO\b", "REPLACE INTO", sql, flags=re.I)
    # utf8mb4's default collation is already case-insensitive, so SQLite's explicit
    # COLLATE NOCASE is redundant here -- and MySQL does not know that collation name.
    sql = re.sub(r"\s+COLLATE\s+NOCASE\b", "", sql, flags=re.I)
    # Namespace our tables away from SwordShine's same-named ones (see _MYSQL_RENAME).
    sql = _MYSQL_RENAME.sub(
        lambda m: "%s %s" % (m.group(1), _MYSQL_PHYSICAL[m.group(2).lower()]), sql)
    sql = _MYSQL_QUALIFIED.sub(
        lambda m: _MYSQL_PHYSICAL[m.group(1).lower()] + ".", sql)
    sql = _mysql_shared_casts(sql, tname)
    return sql


def _mysql_shared_casts(sql, insert_target):
    """Wrap bound parameters that are written to a shared-view column in a different
    encoding (see _MYSQL_WRITE_CAST): a colour int becomes char(6) hex, an epoch becomes a
    DATETIME. The read direction is _adapt_row().

    Doing it here rather than at the call sites is the same bargain _MYSQL_RENAME makes --
    every statement already passes through this translator, so one rule covers the character
    creation, the changeColor handler and the login timestamp alike, and none can be missed.

    It is scoped to statements that TARGET a shared view, because `skin_color` is also a
    real int column on `monsters` and must not be converted there.
    """
    target = (insert_target or "").lower()
    if not target:
        m = re.match(r"\s*UPDATE\s+[\"`]?(\w+)", sql, re.I)
        target = m.group(1).lower() if m else ""
    if target not in _MYSQL_SHARED_VIEWS:
        return sql

    # UPDATE ... SET col=?  ->  SET col=CAST(?)
    def _set(m):
        cast = _MYSQL_WRITE_CAST.get(m.group(2).lower())
        return m.group(0) if cast is None else m.group(1) + m.group(2) + "=" + cast % "?"
    sql = re.sub(r"([,\s(])[\"`]?(\w+)[\"`]?\s*=\s*\?", _set, sql)

    # INSERT INTO t (a,b,c) VALUES (?,?,?)  ->  wrap the placeholder in c's position
    m = re.search(r"INSERT\s+(?:IGNORE\s+)?INTO\s+[\"`]?\w+[\"`]?\s*\(([^)]*)\)\s*"
                  r"VALUES\s*\(([^)]*)\)", sql, re.I)
    if m:
        cols = [c.strip().strip('"').strip("`").lower() for c in m.group(1).split(",")]
        vals = [v.strip() for v in m.group(2).split(",")]
        if len(cols) == len(vals) and any(c in _MYSQL_WRITE_CAST for c in cols):
            vals = [_MYSQL_WRITE_CAST[c] % v if (c in _MYSQL_WRITE_CAST and v == "?") else v
                    for c, v in zip(cols, vals)]
            sql = sql[:m.start(2)] + ", ".join(vals) + sql[m.end(2):]
    return sql


class _MyCursor:
    """Wraps a PyMySQL cursor so rows come back as _PgRow (name- AND position-indexable),
    matching what sqlite3.Row and the Postgres wrapper both give the call sites."""
    __slots__ = ("_cur", "_cols", "_idx")

    def __init__(self, cur):
        self._cur = cur
        self._cols = [d[0] for d in (cur.description or [])]
        self._idx = {c: i for i, c in enumerate(self._cols)}

    def _wrap(self, vals):
        return None if vals is None else _PgRow(self._cols, self._idx,
                                                _adapt_row(self._cols, vals))

    def fetchone(self):
        return self._wrap(self._cur.fetchone())

    def fetchall(self):
        return [self._wrap(v) for v in self._cur.fetchall()]

    def fetchmany(self, size=None):
        rows = self._cur.fetchmany(size) if size is not None else self._cur.fetchmany()
        return [self._wrap(v) for v in rows]

    def __iter__(self):
        for vals in self._cur:
            yield self._wrap(vals)

    @property
    def rowcount(self):
        return self._cur.rowcount

    @property
    def lastrowid(self):
        return self._cur.lastrowid

    @property
    def description(self):
        return self._cur.description

    def close(self):
        self._cur.close()


class _MyConnection:
    """sqlite3.Connection-shaped wrapper over PyMySQL. Mirrors _PgConnection exactly,
    including the `with` that commits on a clean exit without closing."""
    def __init__(self, raw):
        self._raw = raw

    def _run(self, sql, params=()):
        cur = self._raw.cursor()
        if params:
            cur.execute(_to_pyformat(_to_mysql(sql)), tuple(params))
        else:
            # No params: still translate dialect, but skip _to_pyformat so literal `%`
            # in the SQL is not doubled (PyMySQL only scans when args are supplied).
            cur.execute(_to_mysql(sql))
        return _MyCursor(cur)

    def execute(self, sql, params=()):
        return self._run(sql, params)

    def executemany(self, sql, seq):
        cur = self._raw.cursor()
        cur.executemany(_to_pyformat(_to_mysql(sql)), [tuple(p) for p in seq])
        return _MyCursor(cur)

    def executescript(self, script):
        cur = self._raw.cursor()
        for stmt in _split_statements(script):
            cur.execute(_to_mysql(stmt))
        return _MyCursor(cur)

    def commit(self):
        self._raw.commit()

    def rollback(self):
        self._raw.rollback()

    def close(self):
        self._raw.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc_type is None:
            self._raw.commit()
        else:
            self._raw.rollback()
        return False


def _my_connect():
    import pymysql                           # lazy: only the MariaDB path needs it
    from pymysql.constants import CLIENT
    # CLIENT.FOUND_ROWS makes cursor.rowcount report rows MATCHED rather than rows CHANGED,
    # which is what SQLite and Postgres do and what the call sites assume. Without it an
    # UPDATE that writes a column's existing value reports 0 -- e.g. the shop stock
    # decrement, whose CASE deliberately re-writes quantity_remain for unlimited stock,
    # would be read as "sold out".
    raw = pymysql.connect(
        client_flag=CLIENT.FOUND_ROWS,
        host=os.environ.get("INFINITY_MYSQL_HOST", "127.0.0.1"),
        port=int(os.environ.get("INFINITY_MYSQL_PORT", "3306")),
        database=_MY_DB or os.environ.get("INFINITY_MYSQL_DB", "aqwworlds"),
        user=os.environ.get("INFINITY_MYSQL_USER", "swordshine"),
        password=os.environ.get("INFINITY_MYSQL_PASSWORD", ""),
        charset="utf8mb4",
        autocommit=False,
    )
    # ANSI_QUOTES makes `"x"` an identifier quote rather than a string literal, so the
    # column DDL generated by itemrecord/monrecord/shoprecord -- which quotes names the
    # SQL-standard way -- is accepted unchanged. Appended to the server's existing
    # sql_mode rather than replacing it, so nothing else about the session changes.
    with raw.cursor() as cur:
        cur.execute("SET sql_mode = CONCAT(@@sql_mode, ',ANSI_QUOTES')")
        # InnoDB defaults to REPEATABLE READ, so a long-lived connection keeps the snapshot
        # it started with and cannot see rows another connection commits afterwards. SQLite
        # and Postgres both behave as READ COMMITTED here, and the server holds long-lived
        # connections, so without this a handler can serve stale data indefinitely.
        cur.execute("SET SESSION TRANSACTION ISOLATION LEVEL READ COMMITTED")
    raw.commit()
    return _MyConnection(raw)


def _pg_connect():
    import psycopg                          # lazy: only the hosted path needs the dependency
    raw = psycopg.connect(
        host=os.environ.get("INFINITY_PG_HOST", "127.0.0.1"),
        port=int(os.environ.get("INFINITY_PG_PORT", "5432")),
        dbname=os.environ.get("INFINITY_PG_DB", "infinity"),
        user=os.environ.get("INFINITY_PG_USER", "infinity"),
        password=os.environ.get("INFINITY_PG_PASSWORD", ""),
        autocommit=False,
    )
    if _PG_SCHEMA != "public":              # test isolation -> resolve unqualified names here
        with raw.cursor() as cur:
            cur.execute(f'SET search_path TO "{_PG_SCHEMA}"')
        raw.commit()
    return _PgConnection(raw)


def connect():
    if BACKEND in ("mysql", "mariadb"):
        return _my_connect()
    if BACKEND == "postgres":
        return _pg_connect()
    # SQLite (default): unchanged. timeout/busy_timeout ride over OneDrive sync locks
    # (the DB file lives in a synced folder, so a sync can briefly hold it).
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA busy_timeout = 30000;")
    return conn


def _schema_sql():
    """The SCHEMA in the active dialect. Only difference: the two AUTOINCREMENT surrogate
    keys (accounts.id, characters.id) become GENERATED ... AS IDENTITY on Postgres."""
    sql = (SCHEMA.replace("--MONSTERS_COLS--", monrecord.monsters_columns_ddl())
                 .replace("--ITEMS_COLS--", itemrecord.items_columns_ddl())
                 .replace("--SHOPS_COLS--", shoprecord.shops_columns_ddl())
                 .replace("--QUEST_TURNINS_COLS--", qturninrecord.columns_ddl()))
    if BACKEND == "postgres":
        # BY DEFAULT (not ALWAYS) so the data migration can insert rows with their original
        # ids preserved; the identity sequence is then advanced past MAX(id) (see migrate_to_pg).
        # Postgres has no UNSIGNED. The item-reference columns carry it so MySQL can key
        # them against SwordShine's shared `items.id int(11) unsigned`; it is simply dropped
        # here, since an unsigned item id fits a signed INTEGER comfortably.
        sql = re.sub(r"\s+UNSIGNED\b", "", sql, flags=re.I)
        return (re.sub(r"\bREAL\b", "DOUBLE PRECISION", sql).replace("INTEGER PRIMARY KEY AUTOINCREMENT",
                            "INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY")
                   .replace(" BLOB", " BYTEA"))
    if BACKEND in ("mysql", "mariadb"):
        sql = _mysql_shared_identity(sql)
        sql = _mysql_key_text_to_varchar(sql)
        # MySQL's TEXT caps at 65,535 BYTES, where SQLite and Postgres treat TEXT as
        # unbounded. Several columns hold large JSON documents and silently overflow
        # ("Data too long for column 'doc'"), so every remaining TEXT becomes LONGTEXT.
        # Keyed columns were already converted to VARCHAR above and are unaffected.
        sql = re.sub(r"\bTEXT\b", "LONGTEXT", sql)
        # Same 65,535-byte ceiling, same fix: a statue snapshot is a PNG and overflows
        # BLOB ("Data too long for column 'image'"), where SQLite and Postgres treat a
        # blob as unbounded.
        sql = re.sub(r"\bBLOB\b", "LONGBLOB", sql)
        sql = re.sub(r"\bDOUBLE\s+PRECISION\b", "DOUBLE", sql, flags=re.I)
        sql = re.sub(r"\bREAL\b", "DOUBLE", sql)
        sql = sql.replace("INTEGER PRIMARY KEY AUTOINCREMENT",
                          "INTEGER AUTO_INCREMENT PRIMARY KEY")
        # Appended AFTER the dialect rewrites above, which are for OUR tables: the shared
        # DDL is already MySQL and must be emitted verbatim.
        return sql + _MYSQL_SHARED_DDL
    return sql


def _mysql_shared_identity(sql):
    """Take our own accounts/characters/char_items OUT of the schema on MariaDB.

    There, those three names are VIEWS over SwordShine's `users` and `users_items` (created
    by _MYSQL_SHARED_DDL below), so that a player's account, character and bag are one row
    set both games read and write -- the shared inventory. Creating our own tables of the
    same names would shadow them and silently split every player in two.

    Foreign keys pointing AT them are repointed to the base table, because MySQL cannot
    reference a view. The referenced id is the same number either way: the views expose
    `users.id` as the account id AND the character id, which it already was -- Infinity has
    always enforced one character per account (characters.account_id UNIQUE).

    Every char_id/account_id column is declared INTEGER UNSIGNED in SCHEMA for the same
    reason the item-reference columns are: SwordShine's `users.id` is `int(11) unsigned`,
    and MySQL rejects a foreign key whose column signedness differs from its target
    ("Foreign key constraint is incorrectly formed"). Postgres has no UNSIGNED and strips
    it; SQLite gives it INTEGER affinity. So it costs nothing on the other two backends.
    """
    for name in _MYSQL_SHARED_LOGICAL:
        sql = re.sub(r"CREATE\s+TABLE\s+IF\s+NOT\s+EXISTS\s+%s\s*\(.*?\n\);" % name,
                     "", sql, flags=re.I | re.S)
        sql = re.sub(r"CREATE\s+INDEX\s+IF\s+NOT\s+EXISTS\s+\w+\s+ON\s+%s\s*\([^)]*\);"
                     % name, "", sql, flags=re.I)
    sql = re.sub(r"REFERENCES\s+(?:accounts|characters)\s*\(\s*id\s*\)",
                 "REFERENCES users(id)", sql, flags=re.I)
    sql = re.sub(r"REFERENCES\s+char_items\s*\(\s*char_item_id\s*\)",
                 "REFERENCES users_items(id)", sql, flags=re.I)
    return sql


# The shared tables and the views over them. In production SwordShine already owns `users`
# and `users_items`, so the CREATE TABLEs are no-ops and only the views are (re)made -- which
# is deliberate: the ENGINE is the single source of truth for what the views must expose, and
# every boot restates it. The table bodies exist so a throwaway test database is complete
# without a SwordShine install; they carry no foreign keys for that reason.
#
# CREATE OR REPLACE, not IF NOT EXISTS: a view whose definition has drifted from the engine
# that reads it is worse than no view at all.
_MYSQL_SHARED_DDL = """

CREATE TABLE IF NOT EXISTS users (
    id              INT UNSIGNED NOT NULL AUTO_INCREMENT,
    Name            VARCHAR(40)  NOT NULL,
    Hash            VARCHAR(255) NOT NULL,
    Email           VARCHAR(64)  NOT NULL DEFAULT '',
    Gender          CHAR(1)      NOT NULL DEFAULT 'M',
    Gold            INT UNSIGNED NOT NULL DEFAULT 0,
    Coins           INT UNSIGNED NOT NULL DEFAULT 0,
    Level           TINYINT UNSIGNED NOT NULL DEFAULT 1,
    Exp             INT UNSIGNED NOT NULL DEFAULT 0,
    Access          TINYINT UNSIGNED NOT NULL DEFAULT 1,
    HairID          INT UNSIGNED NOT NULL DEFAULT 1,
    ColorSkin       CHAR(6) NOT NULL DEFAULT '000000',
    ColorEye        CHAR(6) NOT NULL DEFAULT '000000',
    ColorHair       CHAR(6) NOT NULL DEFAULT '000000',
    ColorTrim       CHAR(6) NOT NULL DEFAULT '000000',
    ColorAccessory  CHAR(6) NOT NULL DEFAULT '000000',
    DateCreated     DATETIME NOT NULL DEFAULT '1970-01-01 00:00:00',
    LastLogin       DATETIME NOT NULL DEFAULT '1970-01-01 00:00:00',
    UpgradeDays     SMALLINT NOT NULL DEFAULT 0,
    UpgradeExpire   DATETIME NOT NULL DEFAULT '2024-01-01 00:00:00',
    dust            INT NULL,
    class_id        INT NULL,
    stat_str        INT NULL,
    stat_end        INT NULL,
    stat_dex        INT NULL,
    stat_int        INT NULL,
    stat_wis        INT NULL,
    stat_lck        INT NULL,
    achievements    LONGTEXT NULL,
    prefs           LONGTEXT NULL,
    session_token   VARCHAR(191) NULL,
    session_version INT NOT NULL DEFAULT 0,
    token_expires   DOUBLE NOT NULL DEFAULT 0,
    infinity_account_id INT NULL,
    inf_hair_id     INT NULL,
    tracked_quest    INT NOT NULL DEFAULT 0,
    guild_id         INT NOT NULL DEFAULT 0,
    guild_rank       INT NOT NULL DEFAULT 0,
    guild_tag_color  VARCHAR(32)  NOT NULL DEFAULT '',
    owned_tag_colors VARCHAR(255) NOT NULL DEFAULT '[]',
    PRIMARY KEY (id),
    UNIQUE KEY uq_users_name (Name)
);

CREATE TABLE IF NOT EXISTS users_items (
    id              INT UNSIGNED NOT NULL AUTO_INCREMENT,
    UserID          INT UNSIGNED NOT NULL,
    ItemID          INT UNSIGNED NOT NULL,
    EnhID           INT UNSIGNED NULL,
    Equipped        TINYINT UNSIGNED NOT NULL DEFAULT 0,
    Quantity        MEDIUMINT UNSIGNED NOT NULL DEFAULT 1,
    Bank            TINYINT UNSIGNED NOT NULL DEFAULT 0,
    Wear            TINYINT UNSIGNED NOT NULL DEFAULT 0,
    DatePurchased   DATETIME NULL,
    loot_id         INT NOT NULL DEFAULT -1,
    char_pattern_id INT NULL,
    pattern_json    VARCHAR(255) NULL,
    meta            VARCHAR(64) NOT NULL DEFAULT '',
    inf_char_item_id INT NULL,
    PRIMARY KEY (id),
    KEY fk_useritems_userid (UserID)
);

CREATE OR REPLACE VIEW shared_accounts AS
SELECT id AS id, Name AS username, Hash AS password,
       DateCreated AS created, LastLogin AS last_accessed,
       session_token AS session_token, session_version AS session_version,
       token_expires AS token_expires
  FROM users;

CREATE OR REPLACE VIEW shared_characters AS
SELECT id AS id, id AS account_id, Name AS name, Gender AS gender,
       Gold AS gold, Coins AS coins, dust AS dust, Level AS level, Exp AS exp,
       class_id AS class_id, Access AS access_level,
       stat_str AS stat_str, stat_end AS stat_end, stat_dex AS stat_dex,
       stat_int AS stat_int, stat_wis AS stat_wis, stat_lck AS stat_lck,
       UpgradeDays AS upgrade_days, UpgradeExpire AS upgrade_expires,
       ColorSkin AS skin_color, ColorEye AS eye_color, ColorHair AS hair_color,
       ColorTrim AS trim_color, ColorAccessory AS accessory_color,
       HairID AS hair_id, achievements AS achievements, prefs AS prefs,
       tracked_quest AS tracked_quest, guild_id AS guild_id, guild_rank AS guild_rank,
       guild_tag_color AS guild_tag_color, owned_tag_colors AS owned_tag_colors
  FROM users;

CREATE OR REPLACE VIEW shared_char_items AS
SELECT id AS char_item_id, UserID AS char_id, ItemID AS item_id,
       Quantity AS quantity, Equipped AS equipped, Bank AS banked,
       loot_id AS loot_id, char_pattern_id AS char_pattern_id,
       pattern_json AS pattern_json, meta AS meta, DatePurchased AS purchase_date
  FROM users_items;
"""


def _mysql_key_text_to_varchar(sql, width=191):
    """MySQL cannot key a TEXT column without a prefix length ("BLOB/TEXT column used in
    key specification without a key length"). Find every column that takes part in a key --
    inline PRIMARY KEY/UNIQUE, a composite PRIMARY KEY(...)/UNIQUE(...) clause, or a
    CREATE INDEX -- and widen just those TEXT declarations to VARCHAR.

    191 rather than 255: utf8mb4 is 4 bytes per character, and 191*4 stays inside the
    767-byte index limit of older InnoDB row formats.
    """
    # table -> columns named by a CREATE INDEX
    indexed = {}
    for tbl, cols in re.findall(r"CREATE\s+INDEX[^;]*?\bON\s+(\w+)\s*\(([^)]*)\)", sql, re.I):
        indexed.setdefault(tbl.lower(), set()).update(
            c.strip().strip('"`').lower() for c in cols.split(","))

    def fix_table(m):
        head, tbl, body, tail = m.group(1), m.group(2), m.group(3), m.group(4)
        keyed = set(indexed.get(tbl.lower(), ()))
        # `head` already carries the opening paren, so the body is reassembled onto it
        # verbatim -- dropping it silently merged single-line tables into their neighbour.
        # composite clauses: PRIMARY KEY (a, b) / UNIQUE (a, b)
        for cols in re.findall(r"(?:PRIMARY\s+KEY|UNIQUE)\s*\(([^)]*)\)", body, re.I):
            keyed.update(c.strip().strip('"`').lower() for c in cols.split(","))
        # inline: `col TEXT PRIMARY KEY` / `col TEXT UNIQUE NOT NULL`
        for col in re.findall(r"^\s*\"?(\w+)\"?\s+[A-Za-z]+[^,\n]*?\b(?:PRIMARY\s+KEY|UNIQUE)\b",
                              body, re.I | re.M):
            keyed.add(col.lower())

        def widen(cm):
            return cm.group(0) if cm.group(2).lower() not in keyed else \
                "%s%s%sVARCHAR(%d)" % (cm.group(1), cm.group(2), cm.group(3), width)

        # Single-line tables (kv) declare their columns after the paren on the same line,
        # so anchor on an optional line start rather than requiring one.
        body = re.sub(r"(^\s*\"?|\(\s*)(\w+)(\"?\s+)TEXT\b", widen, body, flags=re.I | re.M)
        return head + body + tail

    return re.sub(r"(CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(\w+)\s*\()(.*?)(\);)",
                  fix_table, sql, flags=re.I | re.S)


def _columns(c, table):
    """Column names of a table as a set, in the active dialect (replaces PRAGMA table_info).
    On Postgres scope to the active schema so a throwaway test schema and `public` (which may
    both hold a `characters` table, say) don't bleed into each other."""
    table = _phys(table)
    if BACKEND == "postgres":
        return {r["column_name"] for r in c.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name=? AND table_schema=current_schema()", (table,))}
    if BACKEND in ("mysql", "mariadb"):
        return {r["COLUMN_NAME"] if "COLUMN_NAME" in r.keys() else r["column_name"]
                for r in c.execute(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name=? AND table_schema=DATABASE()", (table,))}
    return {r["name"] for r in c.execute(f"PRAGMA table_info({table})")}


def use_throwaway():
    """Point persistence at a fresh, empty store for an isolated test run - the backend-agnostic
    equivalent of swapping db.DB_PATH to a temp file. On SQLite that's exactly a temp file; on
    Postgres it (re)creates a `throwaway` schema and routes connections there, leaving the real
    `public` data untouched. Call BEFORE db.init()."""
    global DB_PATH, _PG_SCHEMA, _MY_DB
    if BACKEND in ("mysql", "mariadb"):
        # MySQL has no schema-inside-a-database, so the isolated equivalent is a throwaway
        # DATABASE. Needs a user with CREATE privilege; the runtime user does not need it.
        _MY_DB = None                            # connect to the configured db to create
        c = _my_connect()
        name = "infinity_test_" + uuid.uuid4().hex[:24]
        try:
            c.execute("CREATE DATABASE `%s` CHARACTER SET utf8mb4" % name)
            c.commit()
        finally:
            c.close()
        _MY_TEST_DBS.add(name)
        _MY_DB = name
        return
    if BACKEND == "postgres":
        _PG_SCHEMA = "public"
        c = _pg_connect()
        schema = "infinity_test_" + uuid.uuid4().hex
        try:
            c.execute(f'CREATE SCHEMA "{schema}"')
            c.commit()
        finally:
            c.close()
        _PG_TEST_SCHEMAS.add(schema)
        _PG_SCHEMA = schema
    else:
        import tempfile
        DB_PATH = pathlib.Path(tempfile.mkdtemp()) / "throwaway.db"


def cleanup_test_schemas():
    """Only remove unique schemas created by this process, never shared or public data."""
    global _PG_SCHEMA, _MY_DB
    if BACKEND in ("mysql", "mariadb"):
        if not _MY_TEST_DBS:
            return
        _MY_DB = None
        c = _my_connect()
        try:
            for name in list(_MY_TEST_DBS):
                c.execute("DROP DATABASE IF EXISTS `%s`" % name)
                _MY_TEST_DBS.discard(name)
            c.commit()
        finally:
            c.close()
        return
    if not _PG_TEST_SCHEMAS:
        return
    _PG_SCHEMA = "public"
    c = _pg_connect()
    try:
        for schema in list(_PG_TEST_SCHEMAS):
            if not re.fullmatch(r"infinity_test_[0-9a-f]{32}", schema):
                raise ValueError("Invalid test schema")
            c.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
            c.commit()
            _PG_TEST_SCHEMAS.remove(schema)
    finally:
        c.close()


atexit.register(cleanup_test_schemas)


def init():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with connect() as c:
        initialize_connection(c)
    return DB_PATH


def initialize_connection(c):
    """Initialize using the caller's transaction (also used for an empty migration target)."""
    _rename_legacy_pads(c)
    c.executescript(_schema_sql())
    _migrate(c)
    _migrate_monsters(c)
    _migrate_items_columns(c)
    _migrate_shops_columns(c)
    _migrate_quest_turnins(c)
    _migrate_quest_objective_refs(c)
    _import_legacy_pads(c)


def _phys(table):
    """The physical table name on the active backend.

    _to_mysql() rewrites table names inside SQL text, but helpers like _columns() and
    _table_exists() pass a name as a PARAMETER, where that rewrite cannot reach -- asking
    for "classes" would otherwise inspect SwordShine's table of that name instead of ours.
    """
    if BACKEND in ("mysql", "mariadb"):
        return _MYSQL_PHYSICAL.get(table, table)
    return table


def _table_exists(c, name):
    name = _phys(name)
    if BACKEND == "postgres":
        return c.execute("SELECT to_regclass(?)", (name,)).fetchone()[0] is not None
    if BACKEND in ("mysql", "mariadb"):
        return c.execute("SELECT 1 FROM information_schema.tables "
                         "WHERE table_schema=DATABASE() AND table_name=?",
                         (name,)).fetchone() is not None
    return c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                     (name,)).fetchone() is not None


def _rename_legacy_pads(c):
    """The first pad layer stored each PadData as a JSON blob in map_pads.data.
    If we find that shape, move it aside so SCHEMA can create the normalized
    tables; _import_legacy_pads then refills them and drops the old table."""
    if not _table_exists(c, "map_pads"):
        return
    cols = _columns(c, "map_pads")
    if "data" in cols:
        c.execute("DROP TABLE IF EXISTS _map_pads_legacy")
        c.execute("ALTER TABLE map_pads RENAME TO _map_pads_legacy")


def _import_legacy_pads(c):
    if not _table_exists(c, "_map_pads_legacy"):
        return
    import placements                         # lazy: placements imports db
    for r in c.execute("SELECT map, pad_id, data FROM _map_pads_legacy").fetchall():
        try:
            pad = json.loads(r["data"])
        except Exception:
            continue
        placements.write_pad(c, r["map"], pad)
    c.execute("DROP TABLE _map_pads_legacy")


# Per-owner instance fields - they live on a shop offer (shop_items) or an owned
# instance (char_items), never on the shared catalog item.
_ITEM_INSTANCE_FIELDS = ("ShopItemID", "QuantityRemain", "CharItemID", "LootID",
                         "PurchaseDate", "Equipped", "Banked", "ItemPattern")

# Misc Gem. For a gem ITEM, ItemPattern is the gem's catalog stat definition (STR/END/.../Power),
# NOT a per-owner applied gem — so it must NOT be stripped on store, or the gem reads as all-zeroes.
GEM_ITEMTYPE = 43

# Character columns added after the table's first version (additive migration).
_CHARACTER_COLUMNS = [
    "gender TEXT NOT NULL DEFAULT 'M'", "exp INTEGER NOT NULL DEFAULT 0",
    "dust INTEGER NOT NULL DEFAULT 0",
    "class_id INTEGER NOT NULL DEFAULT 0", "access_level INTEGER NOT NULL DEFAULT 0",
    "stat_str INTEGER NOT NULL DEFAULT 0", "stat_end INTEGER NOT NULL DEFAULT 0",
    "stat_dex INTEGER NOT NULL DEFAULT 0", "stat_int INTEGER NOT NULL DEFAULT 0",
    "stat_wis INTEGER NOT NULL DEFAULT 0", "stat_lck INTEGER NOT NULL DEFAULT 0",
    "upgrade_days INTEGER NOT NULL DEFAULT 0",
    "upgrade_expires TEXT NOT NULL DEFAULT '2024-01-01T00:00:00'",
    "skin_color INTEGER NOT NULL DEFAULT 0", "eye_color INTEGER NOT NULL DEFAULT 0",
    "hair_color INTEGER NOT NULL DEFAULT 0", "trim_color INTEGER NOT NULL DEFAULT 0",
    "accessory_color INTEGER NOT NULL DEFAULT 0", "hair_id INTEGER NOT NULL DEFAULT 0",
    "tracked_quest INTEGER NOT NULL DEFAULT 0",
    "achievements TEXT NOT NULL DEFAULT '{}'",
    "prefs TEXT NOT NULL DEFAULT '{}'",
    "guild_id INTEGER NOT NULL DEFAULT 0",       # 0 = no guild
    "guild_rank INTEGER NOT NULL DEFAULT 0",     # 0 member, 1 officer, 2 leader
    "guild_tag_color TEXT NOT NULL DEFAULT ''",  # personal guild-tag colour override ('' = use guild default)
    "owned_tag_colors TEXT NOT NULL DEFAULT '[]'",  # JSON list of purchased tag-colour palette names
]


def item_template(item):
    """The reusable item definition: an item minus per-owner instance fields. Exception: on a
    gem item (ItemType 43), ItemPattern IS the catalog definition (its stats), so it's kept."""
    drop = _ITEM_INSTANCE_FIELDS
    try:
        if int(item.get("ItemType", 0) or 0) == GEM_ITEMTYPE:
            drop = tuple(f for f in drop if f != "ItemPattern")
    except (TypeError, ValueError):
        pass
    return {k: v for k, v in item.items() if k not in drop}


_ITEMCOLS = ",".join(f'"{c}"' for c in itemrecord.ALL_COLS)


def item(conn, item_id):
    """The catalog item wire dict for an item_id, GENERATED from the canonical columns. None
    if the item isn't in the catalog. Replaces `json.loads(SELECT raw FROM items ...)`."""
    row = conn.execute(f"SELECT {_ITEMCOLS} FROM items WHERE id=?", (int(item_id),)).fetchone()
    return itemrecord.to_item(itemrecord.row_to_cols(row)) if row else None


def item_sponsors(conn, item_id):
    """The sponsor credits for an item as the wire strings the client's ItemPreviewNew expects
    (getItemSponsors -> {Sponsors:[...]}). Each entry is the sponsor's name, with a ":false"
    suffix when they have no house on this server (the client splits on the last ':', reads a
    trailing bool as has_house, and hides the "visit home" button when false). Empty list if
    the item has no sponsors. Ordered by seq then name for a stable display."""
    rows = conn.execute(
        "SELECT player_name, has_house FROM item_sponsors WHERE item_id=? ORDER BY seq, player_name",
        (int(item_id),)).fetchall()
    return [r["player_name"] if r["has_house"] else f"{r['player_name']}:false" for r in rows]


_MYSQL_NOTNULL = {}


def _fill_notnull(conn, table, row):
    """Replace NULLs in `row` with something storable, for columns SwordShine declares NOT NULL.

    The shared tables are SwordShine's, and it declares far more columns NOT NULL than our
    item record has opinions about -- Coins, Upgrade, Rarity and a dozen more. Our record
    simply has no value for them and writes NULL, which the database rejects outright
    ("Column 'Coins' cannot be null") rather than falling back to the column default: a
    DEFAULT only applies when the column is OMITTED, and we are naming it.

    So the table's own declaration is read once and used as the fallback. Deriving it from
    the live schema rather than a list here is what stops this drifting the next time
    SwordShine gains a column.
    """
    if table not in _MYSQL_NOTNULL:
        out = {}
        for r in conn.execute(
                "SELECT column_name, column_default, data_type, extra "
                "FROM information_schema.columns "
                "WHERE table_schema=DATABASE() AND table_name=? AND is_nullable='NO'",
                (table,)).fetchall():
            name, dflt, dtype, extra = r[0], r[1], (r[2] or "").lower(), (r[3] or "").lower()
            if "auto_increment" in extra:
                continue
            # A default like `curdate()` is an EXPRESSION, not a literal, and passing the
            # string "curdate()" as a value fails. Those fall through to the type default.
            if dflt is not None and "(" not in str(dflt):
                out[name.lower()] = dflt
            elif dtype in ("int", "bigint", "smallint", "tinyint", "mediumint",
                           "double", "float", "decimal"):
                out[name.lower()] = 0
            elif dtype in ("datetime", "timestamp", "date"):
                out[name.lower()] = "1970-01-01 00:00:00"
            else:
                out[name.lower()] = ""
        _MYSQL_NOTNULL[table] = out
    defaults = _MYSQL_NOTNULL[table]
    for c in list(row):
        if row[c] is None and c.lower() in defaults:
            row[c] = defaults[c.lower()]
    return row


def store_item(conn, it, replace=False):
    """Decompose an item definition (instance fields stripped) into the canonical columns and
    upsert. replace=True overwrites an existing row; default is insert-if-absent."""
    rec = itemrecord.from_dict(item_template(it))
    rec["item_id"] = int(it.get("ID", 0))
    row = itemrecord.cols_to_row(rec)
    if BACKEND in ("mysql", "mariadb"):
        _fill_notnull(conn, "items", row)
    qc = ",".join(f'"{c}"' for c in itemrecord.ALL_COLS)
    ph = ",".join("?" for _ in itemrecord.ALL_COLS)
    action = ("DO UPDATE SET " + ", ".join(f'"{c}"=excluded."{c}"'
              for c in itemrecord.ALL_COLS if c != "item_id")) if replace else "DO NOTHING"
    conn.execute(f"INSERT INTO items ({qc}) VALUES ({ph}) ON CONFLICT(id) {action}",
                 tuple(row[c] for c in itemrecord.ALL_COLS))


_HAIRCOLS = ["id", "Gender", "Name", "File", "unity_file", "bundle"]
_HAIRSEL = "id,Gender,Name,File,unity_file,bundle"


def _hair_wire(row):
    """One `hairs` row as the HairInfo {ID,Name,Filename,Gender,Bundle} the client expects.

    Filename prefers OUR Unity path and falls back to AQW's Flash one, so a hair SwordShine
    captured but we have not still answers with something rather than null -- the client
    dereferences hairBundle.Name without a guard.
    """
    return {"ID": row["id"], "Name": row["Name"],
            "Filename": row["unity_file"] or row["File"],
            "Gender": row["Gender"],
            "Bundle": json.loads(row["bundle"]) if row["bundle"] else None}


def hair(conn, hair_id):
    """A HairInfo {ID,Name,Filename,Gender,Bundle} dict for a hair id (the same wire shape AE's
    GetBaseClasses .hairs entries use), or None."""
    row = conn.execute(f"SELECT {_HAIRSEL} FROM hairs WHERE id=?", (int(hair_id),)).fetchone()
    return None if row is None else _hair_wire(row)


def hairs_list(conn, gender=None):
    """Every hair as HairInfo dicts, optionally filtered to one gender — feeds the /charedit
    picker and the HairShop apop (data/GetBaseClasses .hairs, loadHairShop)."""
    if gender:
        rows = conn.execute(f"SELECT {_HAIRSEL} FROM hairs WHERE Gender=? ORDER BY id",
                            (gender,)).fetchall()
    else:
        rows = conn.execute(f"SELECT {_HAIRSEL} FROM hairs ORDER BY id").fetchall()
    return [_hair_wire(r) for r in rows]


def store_hair(conn, h, replace=False):
    """Upsert one HairInfo dict {ID,Name,Filename,Gender,Bundle}. replace=True overwrites an
    existing row; default is insert-if-absent.

    Identity is the id OR the (Gender, Name) pair, not the id alone. The catalogue is shared
    now, and the two sides do not always agree on a hair's id -- MQElegant is 396 to us and 1
    to SwordShine. Keying on the id alone would let the seed re-insert it under our number and
    split one hairstyle into two rows, which is exactly the duplication the merge removed.
    """
    hid = int(h.get("ID", 0))
    name, gender = h.get("Name"), h.get("Gender")
    bundle = h.get("Bundle")
    bundle = json.dumps(bundle, separators=(",", ":")) if bundle else None
    existing = conn.execute("SELECT id FROM hairs WHERE id=?", (hid,)).fetchone()
    if existing is None and name:
        existing = conn.execute(
            "SELECT id FROM hairs WHERE Gender=? AND LOWER(Name)=LOWER(?)",
            (gender, name)).fetchone()
    if existing is not None:
        if replace:
            conn.execute("UPDATE hairs SET Gender=?, Name=?, unity_file=?, bundle=? WHERE id=?",
                         (gender, name, h.get("Filename"), bundle, existing["id"]))
        return
    conn.execute("INSERT INTO hairs (id, Gender, Name, unity_file, bundle) VALUES (?,?,?,?,?)",
                 (hid, gender, name, h.get("Filename"), bundle))


_SHOPCOLS = ",".join(f'"{c}"' for c in shoprecord.ALL_COLS)


def shop_meta(conn, shop_id):
    """The inner shop meta dict (with items:[]) for a shop_id, generated from the canonical
    columns. None if the shop isn't in the catalog."""
    row = conn.execute(f"SELECT {_SHOPCOLS} FROM shops WHERE shop_id=?", (int(shop_id),)).fetchone()
    return shoprecord.to_meta(shoprecord.row_to_cols(row)) if row else None


def shop_blob(conn, shop_id):
    """The full loadShop wrapper {"Cmd":"loadShop","shop":{...}} for a shop_id, or None.
    Replaces `json.loads(SELECT raw FROM shops ...)`; the live items are re-attached by the caller."""
    meta = shop_meta(conn, shop_id)
    return {"Cmd": "loadShop", "shop": meta} if meta is not None else None


def store_shop(conn, blob, shop_id=None, replace=False):
    """Decompose a loadShop blob (or a bare inner shop dict) into the canonical columns and upsert.
    shop_id defaults to the meta's own shopID; pass it to key by an external id (seed keys by file).
    replace=True overwrites an existing row; default is insert-if-absent."""
    shop = blob.get("shop") if isinstance(blob.get("shop"), dict) else blob
    rec = shoprecord.from_meta(shop)
    rec["shop_id"] = int(shop_id if shop_id is not None else shop.get("shopID", 0))
    row = shoprecord.cols_to_row(rec)
    qc = ",".join(f'"{c}"' for c in shoprecord.ALL_COLS)
    ph = ",".join("?" for _ in shoprecord.ALL_COLS)
    action = ("DO UPDATE SET " + ", ".join(f'"{c}"=excluded."{c}"'
              for c in shoprecord.ALL_COLS if c != "shop_id")) if replace else "DO NOTHING"
    conn.execute(f"INSERT INTO shops ({qc}) VALUES ({ph}) ON CONFLICT(shop_id) {action}",
                 tuple(row[c] for c in shoprecord.ALL_COLS))


# --- quest objectives (turnins): the served `turnin` array is generated from these rows --------
_QT_COLS = ",".join(f'"{c}"' for c in qturninrecord.ALL_COLS)


def quest_turnins(conn, quest_id):
    """A quest's objective list, regenerated from the quest_turnins rows (ordered by idx).
    Replaces parsing quests.raw['turnin'] — the table is the source of truth."""
    rows = conn.execute(f"SELECT {_QT_COLS} FROM quest_turnins WHERE quest_id=? ORDER BY idx",
                        (int(quest_id),)).fetchall()
    return [qturninrecord.to_turnin(qturninrecord.row_to_cols(r)) for r in rows]


def store_quest_turnins(conn, quest_id, turnins):
    """Replace a quest's objective rows from a turnin array (decomposed into canonical columns)."""
    conn.execute("DELETE FROM quest_turnins WHERE quest_id=?", (int(quest_id),))
    qc = ",".join(f'"{c}"' for c in qturninrecord.ALL_COLS)
    ph = ",".join("?" for _ in qturninrecord.ALL_COLS)
    for idx, t in enumerate(turnins or []):
        row = qturninrecord.cols_to_row(qturninrecord.from_turnin(t), int(quest_id), idx)
        conn.execute(f"INSERT INTO quest_turnins ({qc}) VALUES ({ph})",
                     tuple(row[c] for c in qturninrecord.ALL_COLS))


def objective_monsters(conn, qoid):
    """The monster catalog ids whose kill credits objective `qoid` (authored quest_objective_refs)."""
    return {int(r["mon_id"]) for r in conn.execute(
        "SELECT mon_id FROM quest_objective_refs WHERE qoid=?", (int(qoid),))}


def objective_drop(conn, qoid):
    """(chance, min_qty, max_qty) for an objective's probabilistic drop roll, or the deterministic
    default (1.0, 1, 1) if no row — so an unconfigured authored objective still credits +1/kill."""
    r = conn.execute("SELECT chance, min_qty, max_qty FROM quest_objective_drops WHERE qoid=?",
                     (int(qoid),)).fetchone()
    if r is None:
        return (1.0, 1, 1)
    return (float(r["chance"]), int(r["min_qty"]), int(r["max_qty"]))


def _migrate(c):
    """Additive migrations for DBs created before a column existed."""
    cols = _columns(c, "characters")
    for coldef in _CHARACTER_COLUMNS:
        if coldef.split()[0] not in cols:
            c.execute(f"ALTER TABLE characters ADD COLUMN {coldef}")
    # char_items: applied gem (Pattern) for enhanceable items, as a scoped JSON
    # value-object so an item stays empowered/equippable across relogs.
    ci_cols = _columns(c, "char_items")
    if "pattern_json" not in ci_cols:
        c.execute("ALTER TABLE char_items ADD COLUMN pattern_json TEXT")
    if "meta" not in ci_cols:
        c.execute("ALTER TABLE char_items ADD COLUMN meta TEXT NOT NULL DEFAULT ''")
    # quest_rewards: QuestRewardItem.Rate/Hidden (for Roll/Random reward kinds).
    if _table_exists(c, "statues") and "image" not in _columns(c, "statues"):
        c.execute("ALTER TABLE statues ADD COLUMN image "
                  + ("BYTEA" if BACKEND == "postgres" else
                     "LONGBLOB" if BACKEND in ("mysql", "mariadb") else "BLOB"))
    qr_cols = _columns(c, "quest_rewards")
    if "rate" not in qr_cols:
        c.execute("ALTER TABLE quest_rewards ADD COLUMN rate REAL NOT NULL DEFAULT 0")
    if "hidden" not in qr_cols:
        c.execute("ALTER TABLE quest_rewards ADD COLUMN hidden INTEGER NOT NULL DEFAULT 0")
    # classes.rig: the authoritative class visual/particle rig (added 2026-06-15i)
    # classes.resource: per-class resource bar model (updateClass params) (added 2026-06-17, P0-2)
    if _table_exists(c, "classes"):
        cl_cols = _columns(c, "classes")
        if "rig" not in cl_cols:
            c.execute("ALTER TABLE classes ADD COLUMN rig TEXT")
        if "resource" not in cl_cols:
            c.execute("ALTER TABLE classes ADD COLUMN resource TEXT")
    # monsters.catalog: the crawled GetMonsterData def, DB-resident now (R2 removed)
    if _table_exists(c, "skills") and "auto_hold_at_range" not in _columns(c, "skills"):
        c.execute("ALTER TABLE skills ADD COLUMN auto_hold_at_range INTEGER NOT NULL DEFAULT 0")
    if _table_exists(c, "monsters") and "catalog" not in _columns(c, "monsters"):
        c.execute("ALTER TABLE monsters ADD COLUMN catalog TEXT")
    # maps.doc: the full served map doc {area,cells}, DB-resident now (R2 removed)
    if _table_exists(c, "maps") and "doc" not in _columns(c, "maps"):
        c.execute("ALTER TABLE maps ADD COLUMN doc TEXT")
    # accounts.session_token: API-issued token the game-server Login must present (auth)
    if _table_exists(c, "accounts") and "session_token" not in _columns(c, "accounts"):
        c.execute("ALTER TABLE accounts ADD COLUMN session_token TEXT")
    if _table_exists(c, "accounts") and "last_accessed" not in _columns(c, "accounts"):
        c.execute("ALTER TABLE accounts ADD COLUMN last_accessed REAL")
    ac_cols = _columns(c, "accounts")
    for coldef in ("session_version INTEGER NOT NULL DEFAULT 0", "token_expires REAL NOT NULL DEFAULT 0"):
        if coldef.split()[0] not in ac_cols:
            c.execute(f"ALTER TABLE accounts ADD COLUMN {coldef}")
    c.execute("INSERT INTO redeem_code_claims(account_id,code) "
              "SELECT DISTINCT account_id,LOWER(code) FROM redeem_code_uses WHERE 1=1 "
              "ON CONFLICT(account_id,code) DO NOTHING")
    if kv_get(c, "membership_expiry_migration_v1") is None:
        # Preserve previously granted legacy days once; subsequent reads use the real expiry.
        import time
        for row in c.execute("SELECT id,upgrade_days FROM characters WHERE upgrade_days>0 "
                             "AND (upgrade_expires IS NULL OR upgrade_expires='' "
                             "OR upgrade_expires='2024-01-01T00:00:00')").fetchall():
            exp = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(time.time()+int(row['upgrade_days'])*86400))
            c.execute("UPDATE characters SET upgrade_expires=? WHERE id=?", (exp, row['id']))
        kv_set(c, "membership_expiry_migration_v1", "1")
    # guilds.hall_map / hall_data: the guild hall's map + the leader's saved furniture layout
    # (guildhall system — the guild's decoratable shared house). Additive for pre-existing DBs.
    if _table_exists(c, "guilds"):
        g_cols = _columns(c, "guilds")
        if "hall_map" not in g_cols:
            c.execute("ALTER TABLE guilds ADD COLUMN hall_map TEXT NOT NULL DEFAULT ''")
        if "hall_data" not in g_cols:
            c.execute("ALTER TABLE guilds ADD COLUMN hall_data TEXT NOT NULL DEFAULT ''")
        if "tag_color" not in g_cols:
            c.execute("ALTER TABLE guilds ADD COLUMN tag_color TEXT NOT NULL DEFAULT ''")
    _migrate_items(c)
    _migrate_inventory(c)
    _migrate_characters(c)
    _dedupe_accounts(c)


def _dedupe_accounts(c):
    """Enforce case-insensitive username uniqueness. Collapse any existing
    case-dup groups (keep the account with the most owned items, then lowest id;
    delete the empties), then add a UNIQUE index so the DB rejects new dups."""
    for g in c.execute("SELECT LOWER(username) lu FROM accounts "
                       "GROUP BY LOWER(username) HAVING COUNT(*) > 1").fetchall():
        rows = c.execute(
            "SELECT a.id, (SELECT COUNT(*) FROM char_items ci JOIN characters ch "
            "ON ch.id = ci.char_id WHERE ch.account_id = a.id) AS nitems "
            "FROM accounts a WHERE LOWER(username) = ? ORDER BY nitems DESC, a.id ASC",
            (g["lu"],)).fetchall()
        for r in rows[1:]:                       # everything but the richest is a dup
            chars = [x["id"] for x in
                     c.execute("SELECT id FROM characters WHERE account_id=?", (r["id"],))]
            for chid in chars:
                c.execute("DELETE FROM char_items WHERE char_id=?", (chid,))
            c.execute("DELETE FROM characters WHERE account_id=?", (r["id"],))
            c.execute("DELETE FROM accounts WHERE id=?", (r["id"],))
            print(f"[db] removed duplicate account #{r['id']} ({g['lu']!r})")
    if BACKEND in ("mysql", "mariadb"):
        # `accounts` is a view; an index belongs on the table under it. utf8mb3_general_ci
        # is already case-insensitive, so a plain UNIQUE gives the same "one Drathaxie"
        # guarantee -- and it now covers BOTH games, which is what one shared identity means.
        c.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_accounts_username_nocase "
                  "ON users(Name)")
    elif BACKEND == "postgres":             # PG has no COLLATE NOCASE -> functional index
        c.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_accounts_username_nocase "
                  "ON accounts(LOWER(username))")
    else:
        c.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_accounts_username_nocase "
                  "ON accounts(username COLLATE NOCASE)")


def _migrate_inventory(c):
    """Fold the old per-instance `inventory` (full item JSON in `raw`) into
    `char_items` (references the items catalog) + backfill any missing catalog
    items. Idempotent: drops `inventory` once done."""
    if not _table_exists(c, "inventory"):
        return
    for r in c.execute("SELECT * FROM inventory").fetchall():
        item = json.loads(r["raw"])
        item_id = int(item.get("ID", r["item_id"]))
        c.execute(
            "INSERT INTO items(id, name, item_type, raw) VALUES(?,?,?,?) "
            "ON CONFLICT(item_id) DO NOTHING",
            (item_id, item.get("Name"), int(item.get("ItemType", 0) or 0),
             json.dumps(item_template(item), separators=(",", ":"))))
        c.execute(
            "INSERT OR REPLACE INTO char_items(char_item_id, char_id, item_id, quantity, "
            "equipped, banked, loot_id, purchase_date) VALUES(?,?,?,?,?,?,?,?)",
            (r["char_item_id"], r["char_id"], item_id, r["qty"], r["equipped"], 0,
             int(item.get("LootID", -1) if item.get("LootID") is not None else -1),
             item.get("PurchaseDate")))
    c.execute("DROP TABLE inventory")


def _migrate_characters(c):
    """Promote the old `user_json` blob into real character columns, then drop it."""
    cols = _columns(c, "characters")
    if "user_json" not in cols:
        return
    for r in c.execute("SELECT id, user_json FROM characters").fetchall():
        if not r["user_json"]:
            continue
        u = json.loads(r["user_json"])
        cust, stats = u.get("customization") or {}, u.get("stats") or {}
        c.execute(
            "UPDATE characters SET gender=?, stat_str=?, stat_end=?, stat_dex=?, stat_int=?, "
            "stat_wis=?, stat_lck=?, skin_color=?, eye_color=?, hair_color=?, trim_color=?, "
            "accessory_color=?, hair_id=? WHERE id=?",
            (u.get("strGender", "M"), stats.get("STR", 0), stats.get("END", 0),
             stats.get("DEX", 0), stats.get("INT", 0), stats.get("WIS", 0), stats.get("LCK", 0),
             cust.get("SkinColor", 0), cust.get("EyeColor", 0), cust.get("HairColor", 0),
             cust.get("TrimColor", 0), cust.get("AccessoryColor", 0), cust.get("HairID", 0),
             r["id"]))
    c.execute("ALTER TABLE characters DROP COLUMN user_json")


def _migrate_monsters(c):
    """Promote the monsters `raw`/`catalog` JSON blobs into canonical columns (monrecord), then
    drop the blobs. The two wire shapes are generated from the columns thereafter, so they can't
    drift. Idempotent: returns immediately once `raw` is gone."""
    cols = _columns(c, "monsters")
    if "raw" not in cols:
        return
    for col in monrecord.ALL_COLS:
        if col not in cols:
            c.execute(f'ALTER TABLE monsters ADD COLUMN "{col}" {monrecord.COL_TYPES[col]}')
    setcols = [col for col in monrecord.ALL_COLS if col != "mon_id"]
    sets = ", ".join(f'"{col}"=?' for col in setcols)
    for r in c.execute("SELECT mon_id, raw, catalog FROM monsters").fetchall():
        raw = json.loads(r["raw"])
        cat = json.loads(r["catalog"]) if r["catalog"] else {}
        row = monrecord.cols_to_row(monrecord.from_dicts(raw, cat))
        c.execute(f"UPDATE monsters SET {sets} WHERE mon_id=?",
                  tuple(row[col] for col in setcols) + (r["mon_id"],))
    c.execute("ALTER TABLE monsters DROP COLUMN raw")
    c.execute("ALTER TABLE monsters DROP COLUMN catalog")


def _migrate_items_columns(c):
    """Promote the items `raw` JSON blob into canonical columns (itemrecord), then drop raw.
    The item wire dict is generated from the columns thereafter. Idempotent: returns once `raw`
    is gone. Runs after _migrate()'s legacy item backfills so every catalog row is present first."""
    cols = _columns(c, "items")
    if "raw" not in cols:
        return
    for col in itemrecord.ALL_COLS:
        if col not in cols:
            c.execute(f'ALTER TABLE items ADD COLUMN "{col}" {itemrecord.COL_TYPES[col]}')
    setcols = [col for col in itemrecord.ALL_COLS if col != "item_id"]
    sets = ", ".join(f'"{col}"=?' for col in setcols)
    for r in c.execute("SELECT item_id, raw FROM items").fetchall():
        row = itemrecord.cols_to_row(itemrecord.from_dict(json.loads(r["raw"])))
        c.execute(f"UPDATE items SET {sets} WHERE id=?",
                  tuple(row[col] for col in setcols) + (r["item_id"],))
    c.execute("ALTER TABLE items DROP COLUMN raw")


def _migrate_shops_columns(c):
    """Promote the shops `raw` loadShop blob into canonical columns (shoprecord), then drop raw.
    The loadShop meta is generated from the columns thereafter. Idempotent: returns once `raw`
    is gone. Runs after _migrate() so the legacy shop_items normalizer rebuilds raw first."""
    cols = _columns(c, "shops")
    if "raw" not in cols:
        return
    for col in shoprecord.ALL_COLS:
        if col not in cols:
            c.execute(f'ALTER TABLE shops ADD COLUMN "{col}" {shoprecord.COL_TYPES[col]}')
    setcols = [col for col in shoprecord.ALL_COLS if col != "shop_id"]
    sets = ", ".join(f'"{col}"=?' for col in setcols)
    for r in c.execute("SELECT shop_id, raw FROM shops").fetchall():
        blob = json.loads(r["raw"])
        shop = blob.get("shop") if isinstance(blob.get("shop"), dict) else blob
        row = shoprecord.cols_to_row(shoprecord.from_meta(shop))
        c.execute(f"UPDATE shops SET {sets} WHERE shop_id=?",
                  tuple(row[col] for col in setcols) + (r["shop_id"],))
    c.execute("ALTER TABLE shops DROP COLUMN raw")


def _migrate_quest_turnins(c):
    """Activate the quest_turnins table: add any columns added after its first version, then
    populate it from quests.raw if empty (one lossless row per objective). Idempotent — once
    populated, the table is the source of truth and the served turnin array is generated from it."""
    cols = _columns(c, "quest_turnins")
    upgraded = False
    for col in qturninrecord.ALL_COLS:
        if col not in cols:
            c.execute(f'ALTER TABLE quest_turnins ADD COLUMN "{col}" {qturninrecord.COL_TYPES[col]}')
            upgraded = True
    # Re-populate from raw if empty OR just upgraded from the old lossy shape (short $type, no
    # name/_extra). Idempotent: a fully-populated current-shape table is left alone.
    n = c.execute("SELECT COUNT(*) AS n FROM quest_turnins").fetchone()["n"]
    if n and not upgraded:
        return
    c.execute("DELETE FROM quest_turnins")
    qc = ",".join(f'"{x}"' for x in qturninrecord.ALL_COLS)
    ph = ",".join("?" for _ in qturninrecord.ALL_COLS)
    for r in c.execute("SELECT quest_id, raw FROM quests").fetchall():
        for idx, t in enumerate((json.loads(r["raw"]).get("turnin") or [])):
            row = qturninrecord.cols_to_row(qturninrecord.from_turnin(t), r["quest_id"], idx)
            c.execute(f"INSERT INTO quest_turnins ({qc}) VALUES ({ph})",
                      tuple(row[x] for x in qturninrecord.ALL_COLS))


def _migrate_quest_objective_refs(c):
    """Add quest_objective_refs.quest_id (self-describing rows) and backfill it from each
    objective's quest (quest_turnins.qo_id -> quest_id). Idempotent. Runs after quest_turnins
    is populated so the backfill can resolve the quest."""
    if "quest_id" not in _columns(c, "quest_objective_refs"):
        c.execute("ALTER TABLE quest_objective_refs ADD COLUMN quest_id INTEGER NOT NULL DEFAULT 0")
    c.execute(
        "UPDATE quest_objective_refs SET quest_id="
        "COALESCE((SELECT qt.quest_id FROM quest_turnins qt WHERE qt.qo_id=quest_objective_refs.qoid "
        "LIMIT 1), quest_id) WHERE quest_id=0")


def _migrate_items(c):
    """Normalize the old shop_items (full item JSON embedded in `raw`) into the
    `items` catalog + a lean shop_items that references it. Idempotent."""
    si_cols = _columns(c, "shop_items")
    if "raw" in si_cols:                         # old shape -> rebuild normalized
        new_rows = []
        for r in c.execute("SELECT * FROM shop_items").fetchall():
            item = json.loads(r["raw"])
            c.execute(
                "INSERT INTO items(id, name, item_type, raw) VALUES(?,?,?,?) "
                "ON CONFLICT(item_id) DO UPDATE SET "
                "name=excluded.name, item_type=excluded.item_type, raw=excluded.raw",
                (int(item.get("ID", 0)), item.get("Name"),
                 int(item.get("ItemType", 0) or 0),
                 json.dumps(item_template(item), separators=(",", ":"))),
            )
            new_rows.append((r["shop_id"], r["shop_item_id"], r["item_id"],
                             r["cost"], r["coins"],
                             int(item.get("QuantityRemain", -1) or -1)))
        c.execute("ALTER TABLE shop_items RENAME TO _shop_items_old")
        c.execute("""CREATE TABLE shop_items (
            shop_id         INTEGER NOT NULL,
            shop_item_id    INTEGER NOT NULL,
            item_id         INTEGER NOT NULL,
            cost            INTEGER NOT NULL DEFAULT 0,
            coins           INTEGER NOT NULL DEFAULT 0,
            quantity_remain INTEGER NOT NULL DEFAULT -1,
            PRIMARY KEY (shop_id, shop_item_id),
            FOREIGN KEY (item_id) REFERENCES items(id))""")
        c.executemany(
            "INSERT INTO shop_items(shop_id, shop_item_id, item_id, cost, coins, "
            "quantity_remain) VALUES(?,?,?,?,?,?)", new_rows)
        c.execute("DROP TABLE _shop_items_old")

    # Drop any items array still embedded in stored shop-meta blobs. Only relevant while shops
    # still has a `raw` column; _migrate_shops_columns later promotes raw -> canonical columns.
    if "raw" in _columns(c, "shops"):
        for row in c.execute("SELECT shop_id, raw FROM shops").fetchall():
            try:
                blob = json.loads(row["raw"])
            except Exception:
                continue
            shop = blob.get("shop") if isinstance(blob.get("shop"), dict) else blob
            if isinstance(shop.get("items"), list) and shop["items"]:
                shop["items"] = []
                c.execute("UPDATE shops SET raw=? WHERE shop_id=?",
                          (json.dumps(blob, separators=(",", ":")), row["shop_id"]))


def kv_get(conn, key, default=None):
    row = conn.execute("SELECT v FROM kv WHERE k=?", (key,)).fetchone()
    return row["v"] if row else default


def kv_set(conn, key, value):
    conn.execute(
        "INSERT INTO kv(k, v) VALUES(?, ?) "
        "ON CONFLICT(k) DO UPDATE SET v=excluded.v",
        (key, str(value)),
    )
