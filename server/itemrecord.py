"""Canonical item record <-> the item wire dict.

Like monrecord (monsters), this normalizes the `items.raw` JSON blob into editable typed
columns. Items have a SINGLE wire shape (the item definition the client reads), so there is
just one formatter, `to_item`, and `raw` round-trips byte-exact (presence-preserving) through
the columns + an `_extra` catch-all for any field we don't promote.
"""
import json

# item JSON key -> canonical column. Anything not here falls to `_extra` (lossless).
#
# These names are SwordShine's. The two servers share ONE `items` table, owned by
# SwordShine, so where a field is the same fact it maps onto SwordShine's existing column
# rather than a parallel one -- storing it twice is how the two drift apart. MySQL column
# names are case-insensitive, so `name`/`Name` and `cost`/`Cost` already coincide.
#
# Three pairs are NOT the same fact and keep separate columns:
#   ItemType  -> item_type   SwordShine's `Type` is a string carrying family AND wield mode
#                            ("Dagger" = dual-wield of any family); this is family only.
#   EquipSpot -> equip_spot  SwordShine's `Equipment` is 'ar'/'co'/'he'/...; this is numeric.
#   Element   -> element_id  SwordShine's `Element` is a varchar; this is numeric.
FIELD_MAP = {
    "ID": "id", "Name": "Name", "Description": "Description", "ItemType": "item_type",
    "EquipSpot": "equip_spot", "Linkage": "Link", "Icon": "Icon", "Level": "Level",
    "Quantity": "Quantity", "StackSize": "Stack", "Element": "element_id",
    "Faction": "FactionID",
    # strReqQuests is the string form -> SwordShine's ReqQuests; the structured JSON one
    # below keeps its own column. MetaString is byte-identical to SwordShine's Meta
    # (verified against live rows: item 20 = "17", item 61 = "anim:ef_sb,chance:.3").
    "strReqQuests": "ReqQuests", "MetaString": "Meta", "DamageRange": "Range",
    "Rarity": "Rarity", "Filename": "File", "Coins": "Coins", "PrefabName": "prefab_name",
    "MobileCompatibility": "mobile_compat", "Cost": "Cost", "UpgradeOnly": "Upgrade",
    "isClass": "is_class", "DPS": "DPS", "HouseInventory": "house_inventory",
    "RequiredRep": "ReqReputation", "House": "house", "gameFlag": "game_flag",
    "requiredClass": "ReqClassID", "requiredCP": "ReqClassPoints",
    # nested value-objects kept as JSON columns
    "ReqQuests": "req_quests", "boostValues": "boost_values", "Bundle": "bundle",
}
_INV = {v: k for k, v in FIELD_MAP.items()}
JSON_COLS = {"req_quests", "boost_values", "bundle", "_extra"}
SCALAR_COLS = [c for c in FIELD_MAP.values() if c not in JSON_COLS]
ALL_COLS = list(FIELD_MAP.values()) + ["_extra"]   # bundle/req_quests/boost_values already in map

# SQL type per column — validated by a DB round-trip type-fidelity check against all live rows.
# Keyed by the SwordShine column names above. Only used to generate the CREATE TABLE for a
# standalone/test database -- in shared production the `items` table is created and migrated
# by SwordShine, and Infinity's CREATE TABLE IF NOT EXISTS is a no-op against it.
COL_TYPES = {
    "id": "INTEGER UNSIGNED", "Name": "TEXT", "Description": "TEXT", "item_type": "BIGINT",
    "equip_spot": "BIGINT", "Link": "TEXT", "Icon": "TEXT", "Level": "BIGINT",
    "Quantity": "BIGINT", "Stack": "BIGINT", "element_id": "BIGINT", "FactionID": "BIGINT",
    "ReqQuests": "TEXT", "Meta": "TEXT", "Range": "DOUBLE PRECISION",
    "Rarity": "BIGINT", "File": "TEXT", "Coins": "BOOLEAN", "prefab_name": "TEXT",
    "mobile_compat": "BIGINT", "Cost": "BIGINT", "Upgrade": "BOOLEAN", "is_class": "BOOLEAN",
    "DPS": "BIGINT", "house_inventory": "BOOLEAN", "ReqReputation": "BIGINT", "house": "BOOLEAN",
    "game_flag": "BIGINT", "ReqClassID": "BIGINT", "ReqClassPoints": "BIGINT",
    "req_quests": "TEXT", "boost_values": "TEXT", "bundle": "TEXT", "_extra": "TEXT",
}


def from_dict(item):
    """Decompose the item wire dict into one canonical column dict (+ `_extra` for unmapped keys)."""
    cols, extra = {}, {}
    for k, v in (item or {}).items():
        (cols.__setitem__(FIELD_MAP[k], v) if k in FIELD_MAP else extra.__setitem__(k, v))
    cols["_extra"] = extra
    return cols


def to_item(cols):
    """Regenerate the item wire dict — presence-exact and byte-equal with the old raw."""
    out = dict(cols.get("_extra") or {})
    for col, key in _INV.items():
        if col in cols:
            out[key] = cols[col]
    return out


def cols_to_row(cols):
    row = {}
    for c in SCALAR_COLS:
        v = cols.get(c)
        # Real captures carry these as JSON 0/1 (int), not true/false — SQLite accepts either
        # (dynamically typed), but Postgres's BOOLEAN columns reject an int parameter outright
        # (DatatypeMismatch), so coerce here, the one chokepoint every item passes through
        # regardless of backend.
        row[c] = bool(v) if (v is not None and COL_TYPES.get(c) == "BOOLEAN") else v
    for c in JSON_COLS:
        v = cols.get(c)
        row[c] = None if v is None else json.dumps(v, separators=(",", ":"))
    return row


def row_to_cols(row):
    cols = {}
    for c in SCALAR_COLS:
        if c in row.keys() and row[c] is not None:
            v = row[c]
            # SQLite has no real boolean type — a stored True/1 reads back as a plain int, which
            # would then serialize to the wire as "Coins":1 instead of a JSON bool (the client's
            # typed `bool Coins` field expects true/false). Postgres already returns a real bool
            # here; this just makes SQLite agree, mirroring the write-side coercion in cols_to_row.
            cols[c] = bool(v) if COL_TYPES.get(c) == "BOOLEAN" else v
    for c in JSON_COLS:
        v = row[c] if c in row.keys() else None
        if v is not None:
            cols[c] = json.loads(v)
    return cols


def items_columns_ddl():
    return ",\n".join(
        f'    "{c}" {COL_TYPES[c]}' + (" PRIMARY KEY" if c == "id" else "")
        for c in ALL_COLS)
