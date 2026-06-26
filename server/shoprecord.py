"""Canonical shop record <-> the loadShop meta dict.

Like itemrecord (items), this normalizes the `shops.raw` blob into editable typed columns.
The stored raw is a loadShop wrapper around a single `shop` object with an empty items array:
  {"Cmd":"loadShop","shop":{"$type":..,"shopID":N,"Name":..,"Location":..,"items":[],..}}
The constant `Cmd` wrapper and the always-empty `items` array are reconstructed; the inner
shop's flat fields become columns, with an `_extra` catch-all for any unmapped key (lossless).
The live items come from shop_items (re-attached at load time), never from this meta.
"""
import json

# inner shop JSON key -> canonical column. Anything not here falls to `_extra` (lossless).
# "items" is special: never stored (always regenerated as []).
FIELD_MAP = {
    "shopID": "shop_id", "Name": "name", "Location": "location",
    "$type": "type_tag", "gameFlag": "game_flag",
}
_INV = {v: k for k, v in FIELD_MAP.items()}
JSON_COLS = {"_extra"}
SCALAR_COLS = [c for c in FIELD_MAP.values() if c not in JSON_COLS]
ALL_COLS = list(FIELD_MAP.values()) + ["_extra"]

# SQL type per column.
COL_TYPES = {
    "shop_id": "BIGINT", "name": "TEXT", "location": "TEXT",
    "type_tag": "TEXT", "game_flag": "BIGINT", "_extra": "TEXT",
}


def from_meta(shop):
    """Decompose the inner shop dict into one canonical column dict (+ `_extra` for unmapped
    keys). The `items` array is dropped — it's rebuilt from shop_items at load time."""
    cols, extra = {}, {}
    for k, v in (shop or {}).items():
        if k == "items":
            continue
        (cols.__setitem__(FIELD_MAP[k], v) if k in FIELD_MAP else extra.__setitem__(k, v))
    cols["_extra"] = extra
    return cols


def to_meta(cols):
    """Regenerate the inner shop dict (with items:[]) — presence-exact with the old raw."""
    out = dict(cols.get("_extra") or {})
    for col, key in _INV.items():
        if col in cols:
            out[key] = cols[col]
    out["items"] = []
    return out


def cols_to_row(cols):
    row = {}
    for c in SCALAR_COLS:
        row[c] = cols.get(c)
    for c in JSON_COLS:
        v = cols.get(c)
        row[c] = None if v is None else json.dumps(v, separators=(",", ":"))
    return row


def row_to_cols(row):
    cols = {}
    for c in SCALAR_COLS:
        if c in row.keys() and row[c] is not None:
            cols[c] = row[c]
    for c in JSON_COLS:
        v = row[c] if c in row.keys() else None
        if v is not None:
            cols[c] = json.loads(v)
    return cols


def shops_columns_ddl():
    return ",\n".join(
        f'    "{c}" {COL_TYPES[c]}' + (" PRIMARY KEY" if c == "shop_id" else "")
        for c in ALL_COLS)
