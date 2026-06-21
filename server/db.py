"""
SQLite persistence for InfinityServer. Stdlib only — no external deps.

This is the authoritative store for the private server: accounts, characters,
inventory, gold, and the seeded shop catalog. It has zero connection to
Artix Entertainment — accounts live here and here only.

SQLite is the pragmatic start (single file, zero-ops). The schema is plain
SQL, so moving to Postgres later for a hosted/multi-user deployment is a
connection-string swap, not a rewrite.
"""
import json
import os
import sqlite3
import pathlib
from collections.abc import Mapping

DB_PATH = pathlib.Path(__file__).resolve().parent.parent / "data" / "infinity.db"

# Which backend persistence runs against. SQLite stays the zero-ops default (single file,
# fast local tests); Postgres is the hosted path. Selected once at import via INFINITY_DB.
BACKEND = os.environ.get("INFINITY_DB", "sqlite").lower()

# On Postgres, tests get isolation via a throwaway schema (the equivalent of SQLite's temp
# DB file) so they never touch the migrated `public` data. "public" = the real database.
_PG_SCHEMA = "public"

SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT UNIQUE NOT NULL,
    password      TEXT,                  -- PBKDF2 hash (pbkdf2_sha256$iter$salt$hash); legacy plaintext upgrades on login
    created       REAL,
    session_token TEXT                    -- random API-issued token the game-server Login must present
);

-- A character mirrors AQ2D's playerInfo + user: identity, currencies, level/exp,
-- class, the six core stats, and customization colours — all real columns.
-- Equipment is the equipped rows in char_items, not columns here.
CREATE TABLE IF NOT EXISTS characters (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id   INTEGER UNIQUE NOT NULL,
    name         TEXT NOT NULL,
    gender       TEXT NOT NULL DEFAULT 'M',
    gold         INTEGER NOT NULL DEFAULT 0,
    coins        INTEGER NOT NULL DEFAULT 0,
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
CREATE TABLE IF NOT EXISTS items (
    item_id   INTEGER PRIMARY KEY,
    name      TEXT,
    item_type INTEGER,
    raw       TEXT NOT NULL
);

-- One row per item instance a character owns (inventory AND bank). References
-- the shared items catalog — no per-instance item JSON. char_item_id mirrors the
-- wire "CharItemID"; the wire item is rebuilt from items + these instance fields.
CREATE TABLE IF NOT EXISTS char_items (
    char_item_id    INTEGER PRIMARY KEY,
    char_id         INTEGER NOT NULL,
    item_id         INTEGER NOT NULL,
    quantity        INTEGER NOT NULL DEFAULT 1,
    equipped        INTEGER NOT NULL DEFAULT 0,
    banked          INTEGER NOT NULL DEFAULT 0,
    loot_id         INTEGER NOT NULL DEFAULT -1,
    char_pattern_id INTEGER,
    pattern_json    TEXT,                          -- applied gem (Pattern) JSON
    purchase_date   TEXT,
    FOREIGN KEY (char_id) REFERENCES characters(id),
    FOREIGN KEY (item_id) REFERENCES items(item_id)
);
CREATE INDEX IF NOT EXISTS idx_char_items_char ON char_items(char_id);

CREATE TABLE IF NOT EXISTS shops (
    shop_id INTEGER PRIMARY KEY,
    raw     TEXT NOT NULL            -- loadShop meta with an empty items array
);

-- A shop's offer of an item: references items(item_id) and carries only the
-- shop-instance fields (price, currency, stock). No embedded item JSON.
CREATE TABLE IF NOT EXISTS shop_items (
    shop_id         INTEGER NOT NULL,
    shop_item_id    INTEGER NOT NULL,
    item_id         INTEGER NOT NULL,
    cost            INTEGER NOT NULL DEFAULT 0,
    coins           INTEGER NOT NULL DEFAULT 0,
    quantity_remain INTEGER NOT NULL DEFAULT -1,
    PRIMARY KEY (shop_id, shop_item_id),
    FOREIGN KEY (item_id) REFERENCES items(item_id)
);

-- ===================== CATALOG (shared content) ========================
-- Mirrors AQ2D_Server.Game_Engine.*: the "what exists" content the world is
-- built from. Per-character state and the authored pad layer reference these.

-- Monster/NPC catalog (MonID -> definition + art Bundle). Replaces the old
-- in-memory montemplates catalog as the source of truth; pad_npcs.mon_id -> here.
CREATE TABLE IF NOT EXISTS monsters (
    mon_id   INTEGER PRIMARY KEY,
    name     TEXT,
    subtitle TEXT,
    linkage  TEXT,
    hp       INTEGER,
    level    INTEGER,
    race     TEXT,
    element  TEXT,
    behave   TEXT,
    gender   TEXT,
    bundle   TEXT,            -- art AssetBundleData (JSON)
    raw      TEXT NOT NULL,   -- full Monbranch template (JSON) — captured-rich, or derived from catalog
    catalog  TEXT             -- AE GetMonsterData crawled def (JSON), served 1=1 by data/GetMonsterData
);

-- Per-monster drop table: the catalog items a monster can drop, each with its own INDEPENDENT
-- per-kill rate. Authored server content (seeded from data/monster_drops.json). A monster with no
-- rows here falls back to loot.py's shared global pool, so un-authored monsters still drop.
CREATE TABLE IF NOT EXISTS monster_drops (
    mon_id   INTEGER NOT NULL,
    item_id  INTEGER NOT NULL,
    rate     REAL    NOT NULL DEFAULT 0.1,   -- 0..1 independent chance this item drops on a kill
    quantity INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (mon_id, item_id),
    FOREIGN KEY (item_id) REFERENCES items(item_id)
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

CREATE TABLE IF NOT EXISTS quest_turnins (
    quest_id INTEGER NOT NULL,
    idx      INTEGER NOT NULL,
    type     TEXT,                -- $type subclass (Turnin/itemTurnin/...)
    qo_type  INTEGER,
    qo_id    INTEGER,
    item_id  INTEGER,
    quantity INTEGER,
    ref_ids  TEXT,                -- JSON array
    PRIMARY KEY (quest_id, idx),
    FOREIGN KEY (quest_id) REFERENCES quests(quest_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS quest_rewards (
    quest_id INTEGER NOT NULL,
    idx      INTEGER NOT NULL,
    kind     TEXT,                -- Static / random
    item_id  INTEGER,
    quantity INTEGER,
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
                                                 -- ThresholdColor} — DS=determination,
                                                 -- others=mana (per capture)
    raw      TEXT                                -- optional extra class meta
);

-- A skill occupying a slot on a class. The same skill_id can sit on many
-- classes (sfLink shares; sfClone copies into a new id) — hence a join table.
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
    mana         INTEGER NOT NULL DEFAULT 0
);

-- Misc key/value (e.g. the next CharItemID counter).
CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT);

-- Per-character quest PROGRESS (the quest catalog lives in `quests`/`quest_turnins`;
-- these track what each character has accepted/completed and per-objective counters).
CREATE TABLE IF NOT EXISTS char_quests (
    char_id  INTEGER NOT NULL,
    quest_id INTEGER NOT NULL,
    status   INTEGER NOT NULL DEFAULT 1,     -- 1=accepted, 2=completed
    PRIMARY KEY (char_id, quest_id)
);
CREATE TABLE IF NOT EXISTS char_quest_objectives (
    char_id  INTEGER NOT NULL,
    qoid     INTEGER NOT NULL,               -- objective id (Turnin.QOID)
    quantity INTEGER NOT NULL DEFAULT 0,     -- progress toward the objective's required Quantity
    PRIMARY KEY (char_id, qoid)
);

-- Per-character saved house layouts (housesave: [houseMapID, frame, itemsJSON]).
CREATE TABLE IF NOT EXISTS char_houses (
    char_id      INTEGER NOT NULL,
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

-- The NPC(s)/monster(s) on a pad — one row each, every editor NPCEditData field
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


def _pg_row_factory(cursor):
    cols = [c.name for c in cursor.description] if cursor.description else []
    idx = {c: i for i, c in enumerate(cols)}
    return lambda vals: _PgRow(cols, idx, vals)


class _PgConnection:
    """A thin sqlite3.Connection-shaped wrapper over a psycopg connection. Provides
    conn.execute/.executemany/.executescript/.commit/.close and a `with` that commits on a
    clean exit but does NOT close (sqlite3 semantics — the server holds a long-lived conn)."""
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

    def close(self):
        self._raw.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc_type is None:
            self._raw.commit()             # commit on success; leave the connection open
        return False


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
    if BACKEND == "postgres":
        # BY DEFAULT (not ALWAYS) so the data migration can insert rows with their original
        # ids preserved; the identity sequence is then advanced past MAX(id) (see migrate_to_pg).
        return SCHEMA.replace("INTEGER PRIMARY KEY AUTOINCREMENT",
                              "INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY")
    return SCHEMA


def _columns(c, table):
    """Column names of a table as a set, in the active dialect (replaces PRAGMA table_info).
    On Postgres scope to the active schema so a throwaway test schema and `public` (which may
    both hold a `characters` table, say) don't bleed into each other."""
    if BACKEND == "postgres":
        return {r["column_name"] for r in c.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name=? AND table_schema=current_schema()", (table,))}
    return {r["name"] for r in c.execute(f"PRAGMA table_info({table})")}


def use_throwaway():
    """Point persistence at a fresh, empty store for an isolated test run — the backend-agnostic
    equivalent of swapping db.DB_PATH to a temp file. On SQLite that's exactly a temp file; on
    Postgres it (re)creates a `throwaway` schema and routes connections there, leaving the real
    `public` data untouched. Call BEFORE db.init()."""
    global DB_PATH, _PG_SCHEMA
    if BACKEND == "postgres":
        _PG_SCHEMA = "public"               # connect to the real db to (re)create the test schema
        c = _pg_connect()
        c.execute('DROP SCHEMA IF EXISTS throwaway CASCADE')
        c.execute('CREATE SCHEMA throwaway')
        c.commit()
        c.close()
        _PG_SCHEMA = "throwaway"
    else:
        import tempfile
        DB_PATH = pathlib.Path(tempfile.mkdtemp()) / "throwaway.db"


def init():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with connect() as c:
        _rename_legacy_pads(c)      # set aside an old blob-shaped map_pads, if any
        c.executescript(_schema_sql())
        _migrate(c)
        _import_legacy_pads(c)      # backfill the normalized pad tables from it
    return DB_PATH


def _table_exists(c, name):
    if BACKEND == "postgres":
        return c.execute("SELECT to_regclass(?)", (name,)).fetchone()[0] is not None
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


# Per-owner instance fields — they live on a shop offer (shop_items) or an owned
# instance (char_items), never on the shared catalog item.
_ITEM_INSTANCE_FIELDS = ("ShopItemID", "QuantityRemain", "CharItemID", "LootID",
                         "PurchaseDate", "Equipped", "Banked", "ItemPattern")

# Character columns added after the table's first version (additive migration).
_CHARACTER_COLUMNS = [
    "gender TEXT NOT NULL DEFAULT 'M'", "exp INTEGER NOT NULL DEFAULT 0",
    "class_id INTEGER NOT NULL DEFAULT 0", "access_level INTEGER NOT NULL DEFAULT 0",
    "stat_str INTEGER NOT NULL DEFAULT 0", "stat_end INTEGER NOT NULL DEFAULT 0",
    "stat_dex INTEGER NOT NULL DEFAULT 0", "stat_int INTEGER NOT NULL DEFAULT 0",
    "stat_wis INTEGER NOT NULL DEFAULT 0", "stat_lck INTEGER NOT NULL DEFAULT 0",
    "skin_color INTEGER NOT NULL DEFAULT 0", "eye_color INTEGER NOT NULL DEFAULT 0",
    "hair_color INTEGER NOT NULL DEFAULT 0", "trim_color INTEGER NOT NULL DEFAULT 0",
    "accessory_color INTEGER NOT NULL DEFAULT 0", "hair_id INTEGER NOT NULL DEFAULT 0",
    "tracked_quest INTEGER NOT NULL DEFAULT 0",
    "achievements TEXT NOT NULL DEFAULT '{}'",
    "prefs TEXT NOT NULL DEFAULT '{}'",
]


def item_template(item):
    """The reusable item definition: an item minus per-owner instance fields."""
    return {k: v for k, v in item.items() if k not in _ITEM_INSTANCE_FIELDS}


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
    # classes.rig: the authoritative class visual/particle rig (added 2026-06-15i)
    # classes.resource: per-class resource bar model (updateClass params) (added 2026-06-17, P0-2)
    if _table_exists(c, "classes"):
        cl_cols = _columns(c, "classes")
        if "rig" not in cl_cols:
            c.execute("ALTER TABLE classes ADD COLUMN rig TEXT")
        if "resource" not in cl_cols:
            c.execute("ALTER TABLE classes ADD COLUMN resource TEXT")
    # monsters.catalog: the crawled GetMonsterData def, DB-resident now (R2 removed)
    if _table_exists(c, "monsters") and "catalog" not in _columns(c, "monsters"):
        c.execute("ALTER TABLE monsters ADD COLUMN catalog TEXT")
    # maps.doc: the full served map doc {area,cells}, DB-resident now (R2 removed)
    if _table_exists(c, "maps") and "doc" not in _columns(c, "maps"):
        c.execute("ALTER TABLE maps ADD COLUMN doc TEXT")
    # accounts.session_token: API-issued token the game-server Login must present (auth)
    if _table_exists(c, "accounts") and "session_token" not in _columns(c, "accounts"):
        c.execute("ALTER TABLE accounts ADD COLUMN session_token TEXT")
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
    if BACKEND == "postgres":               # PG has no COLLATE NOCASE -> functional index
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
            "INSERT INTO items(item_id, name, item_type, raw) VALUES(?,?,?,?) "
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


def _migrate_items(c):
    """Normalize the old shop_items (full item JSON embedded in `raw`) into the
    `items` catalog + a lean shop_items that references it. Idempotent."""
    si_cols = _columns(c, "shop_items")
    if "raw" in si_cols:                         # old shape -> rebuild normalized
        new_rows = []
        for r in c.execute("SELECT * FROM shop_items").fetchall():
            item = json.loads(r["raw"])
            c.execute(
                "INSERT INTO items(item_id, name, item_type, raw) VALUES(?,?,?,?) "
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
            FOREIGN KEY (item_id) REFERENCES items(item_id))""")
        c.executemany(
            "INSERT INTO shop_items(shop_id, shop_item_id, item_id, cost, coins, "
            "quantity_remain) VALUES(?,?,?,?,?,?)", new_rows)
        c.execute("DROP TABLE _shop_items_old")

    # Drop any items array still embedded in stored shop-meta blobs.
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
