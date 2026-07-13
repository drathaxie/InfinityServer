"""Canonical monster/NPC record <-> the two wire shapes.

The `monsters` table historically stored two overlapping JSON blobs per row:
  * raw     — the spawn monBranch (placement-rich; served by area_payload)
  * catalog — the GetMonsterData def (served by data/GetMonsterData; the NPC portrait path)
They drifted (177/411 rows), which is how a stale `catalog` broke Tato's apop portrait.

This module makes ONE canonical column record the source of truth and GENERATES both
shapes from it, so they can never drift again — and the scalar fields become directly
editable columns instead of JSON surgery.

Authority rule (confirmed): `raw` wins for every shared field (it is the placement-rich
captured truth); `catalog` contributes only its own customization fields (colours, hair,
scale range, overrides). `to_monbranch` is byte-exact with the old raw (spawns never
change); `to_catalog` is regenerated and ENRICHED (gains the correct element/apopID/
Bundle/placement the thin crawl was missing).
"""
import json

# monBranch (raw) key  ->  canonical column.  raw is authoritative + presence-defining.
RAW_MAP = {
    "MonID": "mon_id", "ID": "id_legacy", "strMonName": "name", "strSubtitle": "subtitle",
    "strLinkage": "linkage", "intHP": "hp", "intHPMax": "hp_max", "sRace": "race",
    "strElement": "element", "Level": "level", "Gender": "gender", "Class": "class_id",
    "strBehave": "behave", "strFrame": "frame", "Scale": "scale", "apopID": "apop_id",
    "MonMapID": "mon_map_id", "x": "x", "y": "y", "fx": "fx", "fy": "fy",
    "intState": "state", "reactionType": "reaction_type", "direction": "direction",
    "NoMove": "no_move", "DisableHitFlash": "disable_hit_flash",
    "NPCRequirementData": "npc_req", "pvpTeam": "pvp_team",
    "Bundle": "bundle", "equippedItems": "equipped_items",
}
# catalog-only key  ->  canonical column (avatar customization, scale range, overrides).
CAT_ONLY_MAP = {
    "bRed": "b_red", "intRSS": "int_rss", "scaleMin": "scale_min", "scaleMax": "scale_max",
    "nameOverride": "name_override", "levelOverride": "level_override",
    "SkinColor": "skin_color", "HairColor": "hair_color", "EyeColor": "eye_color",
    "BaseColor": "base_color", "TrimColor": "trim_color", "AccessoryColor": "accessory_color",
    "HairID": "hair_id", "HPScale": "hp_scale", "MPMax": "mp_max",
    "HairFilename": "hair_filename", "HairName": "hair_name",
}
_RAW_INV = {v: k for k, v in RAW_MAP.items()}
_CAT_INV = {v: k for k, v in CAT_ONLY_MAP.items()}

# Which raw-derived (shared) fields belong in the GetMonsterData catalog shape — the
# identity/placement keys the AE crawl carries (NOT spawn-only extras like fx/fy/NoMove/
# direction/MonMapID/reactionType, which the crawl never had).
CAT_FROM_RAW = ["mon_id", "id_legacy", "name", "subtitle", "linkage", "bundle", "hp",
                "hp_max", "frame", "race", "element", "behave", "level", "state", "scale",
                "apop_id", "equipped_items", "gender", "class_id", "x", "y"]

# Columns persisted as JSON text (everything else is a scalar column).
JSON_COLS = {"bundle", "equipped_items", "_raw_extra", "_cat_extra"}
# All scalar columns, in a stable order (for CREATE TABLE / migration).
SCALAR_COLS = [c for c in (list(RAW_MAP.values()) + list(CAT_ONLY_MAP.values()))
               if c not in JSON_COLS]


def from_dicts(raw, cat):
    """Decompose the two wire dicts into one canonical column dict. `raw` wins on shared
    fields; `cat`'s own fields are kept; aliases/odd keys are preserved in per-shape extras."""
    cols, raw_extra, cat_extra = {}, {}, {}
    for k, v in (raw or {}).items():
        (cols.__setitem__(RAW_MAP[k], v) if k in RAW_MAP else raw_extra.__setitem__(k, v))
    for k, v in (cat or {}).items():
        if k in RAW_MAP:
            # shared field: raw wins when present; otherwise keep the catalog's value so the
            # NPC/portrait path never loses a field raw happened not to carry (e.g. Class/ID).
            if RAW_MAP[k] not in cols:
                cat_extra[k] = v
        elif k in CAT_ONLY_MAP:
            cols[CAT_ONLY_MAP[k]] = v
        else:
            cat_extra[k] = v                            # Name/Subtitle/scale aliases, etc.
    cols["_raw_extra"] = raw_extra
    cols["_cat_extra"] = cat_extra
    return cols


def to_monbranch(cols):
    """Regenerate the spawn monBranch — presence-exact and byte-equal with the old raw."""
    out = dict(cols.get("_raw_extra") or {})
    for col, key in _RAW_INV.items():
        if col in cols:
            out[key] = cols[col]
    return out


def to_catalog(cols):
    """Regenerate the GetMonsterData catalog — enriched from the canonical record."""
    out = dict(cols.get("_cat_extra") or {})
    for col in CAT_FROM_RAW:                            # shared identity, enriched from raw
        if col in cols:
            out[_RAW_INV[col]] = cols[col]
    for col, key in _CAT_INV.items():                  # avatar customization
        if col in cols:
            out[key] = cols[col]
    if "equippedItems" in out:
        out["equippedItems"] = _equipped_items_for_client(out.get("equippedItems"))
    if not out.get("Scale"):       # a scale-less/zero stub row renders at scale 0 (invisible) —
        out["Scale"] = 1.0         # every monster must have a render scale; default to full size.
    return out


def _equipped_items_for_client(equipped):
    """Return the GetMonsterData equippedItems shape Unity's Monbranch can deserialize.

    Custom authoring paths have produced both AE-style arrays and our editor's canonical
    {equipSpot: itemDef} object. The client field is Dictionary<EquipSpots, EquipItem>,
    so the wire shape must be a JSON object keyed by the numeric equip slot.
    """
    if not equipped:
        return {}
    if isinstance(equipped, dict):
        return {str(k): v for k, v in equipped.items()}
    if isinstance(equipped, list):
        out = {}
        for item in equipped:
            if not isinstance(item, dict):
                continue
            spot = item.get("EquipSpot")
            if spot is None:
                spot = item.get("equipSpot")
            if spot is not None:
                out[str(spot)] = item
        return out
    return {}


# ---- DB row <-> canonical cols (JSON columns are stored as text) --------------
def cols_to_row(cols):
    """canonical dict -> a {column: storable_value} mapping for INSERT/UPDATE."""
    row = {}
    for c in SCALAR_COLS:
        v = cols.get(c)
        # Real captures can carry a bool column as JSON 0/1 (int) rather than true/false —
        # SQLite tolerates either, but Postgres's BOOLEAN columns reject an int param outright
        # (DatatypeMismatch); coerce here so both backends accept whatever the capture had.
        row[c] = bool(v) if (v is not None and COL_TYPES.get(c) == "BOOLEAN") else v
    for c in JSON_COLS:
        v = cols.get(c)
        row[c] = None if v is None else json.dumps(v, separators=(",", ":"))
    return row


def row_to_cols(row):
    """a DB row (mapping) -> canonical dict (JSON columns parsed back)."""
    cols = {}
    for c in SCALAR_COLS:
        if c in row.keys() and row[c] is not None:
            v = row[c]
            # SQLite has no real boolean type — a stored True/1 reads back as a plain int, which
            # would then serialize to the wire as an int instead of a JSON bool. Postgres already
            # returns a real bool here; this makes SQLite agree (mirrors cols_to_row's write side).
            cols[c] = bool(v) if COL_TYPES.get(c) == "BOOLEAN" else v
    for c in JSON_COLS:
        v = row[c] if c in row.keys() else None
        if v is not None:
            cols[c] = json.loads(v)
    return cols


# ---- schema -----------------------------------------------------------------
# SQL type per column, validated by a DB round-trip type-fidelity check against all live rows
# (BIGINT/TEXT round-trip exactly; the DOUBLE fields accept the occasional integer JSON value as
# 1 -> 1.0, which the client's `double`/`float` Monbranch fields parse identically).
COL_TYPES = {
    "mon_id": "BIGINT", "id_legacy": "BIGINT", "name": "TEXT", "subtitle": "TEXT",
    "linkage": "TEXT", "hp": "BIGINT", "hp_max": "BIGINT", "race": "TEXT", "element": "TEXT",
    "level": "BIGINT", "gender": "TEXT", "class_id": "BIGINT", "behave": "TEXT", "frame": "TEXT",
    "scale": "DOUBLE PRECISION", "apop_id": "BIGINT", "mon_map_id": "BIGINT",
    "x": "DOUBLE PRECISION", "y": "DOUBLE PRECISION", "fx": "DOUBLE PRECISION",
    "fy": "DOUBLE PRECISION", "state": "BIGINT", "reaction_type": "BIGINT", "direction": "BIGINT",
    "no_move": "BOOLEAN", "disable_hit_flash": "BOOLEAN", "npc_req": "TEXT", "pvp_team": "BIGINT",
    "b_red": "BIGINT", "int_rss": "BIGINT", "scale_min": "DOUBLE PRECISION",
    "scale_max": "DOUBLE PRECISION", "name_override": "TEXT", "level_override": "BIGINT",
    "skin_color": "BIGINT", "hair_color": "BIGINT", "eye_color": "BIGINT", "base_color": "BIGINT",
    "trim_color": "BIGINT", "accessory_color": "BIGINT", "hair_id": "BIGINT",
    "hp_scale": "DOUBLE PRECISION", "mp_max": "BIGINT", "hair_filename": "TEXT", "hair_name": "TEXT",
    "bundle": "TEXT", "equipped_items": "TEXT", "_raw_extra": "TEXT", "_cat_extra": "TEXT",
}
# every column, mon_id first (PK), in a stable order for CREATE TABLE / INSERT.
ALL_COLS = SCALAR_COLS + ["bundle", "equipped_items", "_raw_extra", "_cat_extra"]


def monsters_columns_ddl():
    """The column body for `CREATE TABLE monsters (...)`, generated so the schema can never drift
    from the (de)composition logic above."""
    return ",\n".join(
        f'    "{c}" {COL_TYPES[c]}' + (" PRIMARY KEY" if c == "mon_id" else "")
        for c in ALL_COLS)
