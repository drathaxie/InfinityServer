"""Canonical quest-objective (Turnin) record <-> a quest's `turnin` array entry.

A quest's `turnin` array (in quests.raw) is a list of polymorphic objective objects — the
client's Quest+Turnin / itemTurnin / WatchCutsceneTurnin / OpenApopTurnin / interactTurnin
subclasses (QuestObjectiveType: Turnin=0, Killcount=1, Interact=2, Talk=3, Apop=4, Cutscene=5).
Like itemrecord, this normalizes each objective into one quest_turnins row so objective crediting
is table-driven instead of re-parsed from raw, and the served turnin array is REGENERATED from the
rows (lossless via an `_extra` catch-all — round-trips byte-equal with the original).

Row metadata (not objective fields): quest_id (the parent quest) and idx (position in the array).
QuestID is also carried as a field and equals quest_id.
"""
import json

# Turnin JSON key -> canonical column. Anything not here falls to `_extra` (lossless).
FIELD_MAP = {
    "$type": "type", "QOType": "qo_type", "QOID": "qo_id", "QuestID": "quest_id",
    "ItemID": "item_id", "Quantity": "quantity", "Name": "name", "RefIDs": "ref_ids",
    "ApopID": "apop_id", "DialogID": "dialog_id", "MapID": "map_id", "Chance": "chance",
    "MsgFail": "msg_fail", "MsgSuccess": "msg_success",
}
_INV = {v: k for k, v in FIELD_MAP.items()}
JSON_COLS = {"_extra"}
SCALAR_COLS = [c for c in FIELD_MAP.values() if c not in JSON_COLS]
# quest_id/idx are the row key; the rest are the objective's own columns.
OBJ_COLS = [c for c in FIELD_MAP.values() if c != "quest_id"] + ["_extra"]
ALL_COLS = ["quest_id", "idx"] + OBJ_COLS

COL_TYPES = {
    "quest_id": "INTEGER", "idx": "INTEGER", "type": "TEXT", "qo_type": "INTEGER",
    "qo_id": "INTEGER", "item_id": "INTEGER", "quantity": "INTEGER", "name": "TEXT",
    "ref_ids": "TEXT", "apop_id": "INTEGER", "dialog_id": "INTEGER", "map_id": "INTEGER",
    "chance": "INTEGER", "msg_fail": "TEXT", "msg_success": "TEXT", "_extra": "TEXT",
}


def from_turnin(t):
    """Decompose one objective dict into a column dict (+ `_extra` for unmapped keys)."""
    cols, extra = {}, {}
    for k, v in (t or {}).items():
        (cols.__setitem__(FIELD_MAP[k], v) if k in FIELD_MAP else extra.__setitem__(k, v))
    cols["_extra"] = extra
    return cols


def to_turnin(cols):
    """Regenerate the objective dict — presence-exact and byte-equal with the old raw entry."""
    out = dict(cols.get("_extra") or {})
    for col, key in _INV.items():
        if col in cols:
            out[key] = cols[col]
    return out


def cols_to_row(cols, quest_id, idx):
    row = {"quest_id": quest_id, "idx": idx}
    for c in OBJ_COLS:
        if c in JSON_COLS:
            v = cols.get(c)
            row[c] = None if v is None else json.dumps(v, separators=(",", ":"))
        else:
            row[c] = cols.get(c)
    return row


def row_to_cols(row):
    cols = {}
    # restore QuestID (the parent key doubles as the objective's QuestID field)
    if "quest_id" in row.keys() and row["quest_id"] is not None:
        cols["quest_id"] = row["quest_id"]
    for c in OBJ_COLS:
        if c in JSON_COLS:
            v = row[c] if c in row.keys() else None
            if v is not None:
                cols[c] = json.loads(v)
        elif c in row.keys() and row[c] is not None:
            cols[c] = row[c]
    return cols


def columns_ddl():
    cols = ",\n".join(f'    "{c}" {COL_TYPES[c]}' for c in ALL_COLS)
    return cols + ',\n    PRIMARY KEY (quest_id, idx)'
