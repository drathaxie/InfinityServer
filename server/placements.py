"""
Authored NPC/monster placement layer ("pads") — the server side of in-game NPC
editing for InfinityServer.

Why this exists / how the client works (reversed from the decompiled client):
  Every NPC and monster on a map is built by the client *purely* from the
  `monBranch` array inside the AreaJoin packet the SERVER sends
  (Area.OnMapLoaded -> `new Monster(mb.MonMapID, mb)`, positioned at mb.x/mb.y,
  Frame mb.strFrame). Nothing is baked into the map .unity3d bundle. So:
    * to REMOVE an NPC  -> omit it from the monBranch we serve
    * to ADD an NPC     -> append a monBranch entry (free x/y; art from its Bundle)

The in-game dev editor (NPCEdit) speaks a richer shape: a dict of `PadData` (a
placement slot) each holding a list of `NPCEditData` (the monsters on it). On
open it sends `GetMapSpawns` and expects an s2c `MapPadData`; on save it fires
`SavePad`/`AddMon`/`AddNewPad`/`monDelete`/`padDelete`. Edits show on map RELOAD.

Storage is fully relational (no JSON blobs): `map_pads` holds the placement
(position/frame), `pad_npcs` holds one row per NPC with every editor field as a
column. The editor's PadData/NPCEditData are reconstructed from those columns,
and the served monBranch is compiled from them — each NPC's art/behaviour merged
from its MonID's canonical captured Monbranch (montemplates).
"""
import json
import pathlib

import montemplates

MAPS_DIR = pathlib.Path(__file__).resolve().parent.parent / "data" / "maps"

# New pads start here so their ids never collide with captured MonMapIDs (~<2200)
# or with the compiled ids we mint for 2nd+ NPCs on a pad (900000+).
NEW_PAD_BASE = 9000

# Column maps: (editor JSON key, db column, default). Single source of truth for
# translating between the client's PadData/NPCEditData and our tables.
PAD_COLS = [
    ("X", "x", 0.0), ("Y", "y", 0.0), ("MapID", "area_id", 0),
    ("Frame", "frame", "Enter"), ("Direction", "direction", 1),
    ("RequirementData", "requirement_data", None),
]
NPC_COLS = [
    ("ID", "mon_id", 0), ("Name", "name", ""), ("Level", "level", 1),
    ("apopID", "apop_id", -1), ("Subtitle", "subtitle", ""), ("MaxHP", "max_hp", 100),
    ("scaleMin", "scale_min", 1.0), ("scaleMax", "scale_max", 1.0),
    ("Gold", "gold", 0), ("Exp", "exp", 0), ("Rep", "rep", 0),
    ("NoTurn", "no_turn", False), ("NoMove", "no_move", False),
    ("Unkillable", "unkillable", False), ("DeathAtPercent", "death_at_percent", 0),
    ("Boss", "boss", False), ("Element", "element", 0), ("Race", "race", 0),
    ("Agro", "agro", 0), ("ClassID", "class_id", 0),
    ("SkinColor", "skin_color", 0), ("HairColor", "hair_color", 0),
    ("EyeColor", "eye_color", 0), ("BaseColor", "base_color", 0),
    ("TrimColor", "trim_color", 0), ("AccessoryColor", "accessory_color", 0),
    ("HairID", "hair_id", 0),
]


def _safe(name):
    return "".join(ch if ch.isalnum() else "_" for ch in (name or "")).lower()


def _captured_area(map_name):
    """The captured AreaJoin dict for a map (or None)."""
    p = MAPS_DIR / f"{_safe(map_name)}.json"
    if not p.exists():
        return None
    try:
        return (json.loads(p.read_text(encoding="utf-8")) or {}).get("area")
    except Exception:
        return None


# ---- column <-> editor-dict translation -----------------------------------

def _to_col(default, value):
    if value is None:
        value = default
    return (1 if value else 0) if isinstance(default, bool) else value


def _npc_dict(row):
    """Reconstruct an NPCEditData dict from a pad_npcs row."""
    out = {}
    for jk, col, default in NPC_COLS:
        out[jk] = bool(row[col]) if isinstance(default, bool) else row[col]
    return out


def _pad_dict(pad_row, npc_rows):
    """Reconstruct a PadData dict from a map_pads row + its pad_npcs rows."""
    out = {"ID": pad_row["pad_id"]}
    for jk, col, _ in PAD_COLS:
        out[jk] = pad_row[col]
    out["NPCData"] = [_npc_dict(r) for r in npc_rows]
    return out


def write_pad(conn, map_name, pad):
    """Upsert a whole PadData dict (with its NPCData) into the relational tables.
    Used by seeding, AddNewPad, SavePad, and legacy-blob import."""
    pad_id = int(pad.get("ID", 0))
    cols = ["map", "pad_id"] + [c for _, c, _ in PAD_COLS]
    vals = [map_name, pad_id] + [_to_col(d, pad.get(jk)) for jk, _, d in PAD_COLS]
    # full-row upsert (cols == every map_pads column) — dialect-neutral equivalent of the
    # old INSERT OR REPLACE; valid on SQLite (>=3.24) and Postgres alike.
    upd = ", ".join(f"{c}=excluded.{c}" for c in cols if c not in ("map", "pad_id"))
    conn.execute(
        f"INSERT INTO map_pads({','.join(cols)}) "
        f"VALUES({','.join('?' * len(cols))}) "
        f"ON CONFLICT(map, pad_id) DO UPDATE SET {upd}", vals)
    conn.execute("DELETE FROM pad_npcs WHERE map=? AND pad_id=?", (map_name, pad_id))
    for slot, npc in enumerate(pad.get("NPCData") or []):
        _write_npc(conn, map_name, pad_id, slot, npc)


def _write_npc(conn, map_name, pad_id, slot, npc):
    cols = ["map", "pad_id", "slot"] + [c for _, c, _ in NPC_COLS]
    vals = [map_name, pad_id, slot] + [_to_col(d, npc.get(jk)) for jk, _, d in NPC_COLS]
    # full-row upsert (cols == every pad_npcs column) — dialect-neutral equivalent of the
    # old INSERT OR REPLACE; valid on SQLite (>=3.24) and Postgres alike.
    upd = ", ".join(f"{c}=excluded.{c}" for c in cols if c not in ("map", "pad_id", "slot"))
    conn.execute(
        f"INSERT INTO pad_npcs({','.join(cols)}) "
        f"VALUES({','.join('?' * len(cols))}) "
        f"ON CONFLICT(map, pad_id, slot) DO UPDATE SET {upd}", vals)


# ---- seeding from captured monBranch --------------------------------------

def _npc_from_monbranch(mb):
    """An NPCEditData dict derived from a captured monBranch entry."""
    hp = mb.get("intHPMax") or mb.get("intHP") or 100
    return {
        "ID": mb.get("MonID"), "Level": mb.get("Level", 1),
        "Name": mb.get("strMonName", ""), "apopID": mb.get("apopID", -1),
        "scaleMin": mb.get("Scale", 1.0), "scaleMax": mb.get("Scale", 1.0),
        "Subtitle": mb.get("strSubtitle", ""), "MaxHP": hp,
        "NoTurn": bool(mb.get("NoTurn", False)), "NoMove": bool(mb.get("NoMove", False)),
        "SkinColor": mb.get("SkinColor", 0), "HairColor": mb.get("HairColor", 0),
        "EyeColor": mb.get("EyeColor", 0), "BaseColor": mb.get("BaseColor", 0),
        "TrimColor": mb.get("TrimColor", 0), "AccessoryColor": mb.get("AccessoryColor", 0),
        "HairID": mb.get("HairID", 0),
    }


def _pad_from_monbranch(mb, area_id):
    """A PadData dict for one captured monBranch entry. pad id reuses MonMapID so
    Sight's 'PAD n' and m:<id> targeting stay consistent."""
    return {
        "ID": int(mb.get("MonMapID", 0)),
        "X": mb.get("x", 0.0), "Y": mb.get("y", 0.0), "MapID": area_id,
        "Frame": mb.get("strFrame", "Enter"),
        "Direction": int(mb.get("direction", 1) or 0),
        "RequirementData": mb.get("NPCRequirementData"),
        "NPCData": [_npc_from_monbranch(mb)],
    }


def _npc_from_template(conn, mon_id):
    """A default NPCEditData for a freshly-added monster, from its template."""
    tmpl = montemplates.template(conn, mon_id)
    return {
        "ID": int(mon_id), "Level": tmpl.get("Level", 1),
        "Name": tmpl.get("strMonName", f"Mon {mon_id}"),
        "apopID": tmpl.get("apopID", -1), "Subtitle": tmpl.get("strSubtitle", ""),
        "MaxHP": tmpl.get("intHPMax", 100),
    }


# ---- pad -> monBranch compilation ------------------------------------------

def _compile_pad(conn, pad_row, npc_rows):
    """Compile a pad's NPC rows into monBranch entries (art from each template)."""
    out = []
    pad_id = pad_row["pad_id"]
    for i, npc in enumerate(npc_rows):
        mon_id = npc["mon_id"]
        mb = montemplates.template(conn, mon_id)
        mon_map_id = pad_id if i == 0 else 900000 + pad_id * 10 + i
        hp = npc["max_hp"] or mb.get("intHPMax") or 100
        mb.update({
            "MonID": int(mon_id), "ID": int(mon_id), "MonMapID": mon_map_id,
            "x": pad_row["x"], "y": pad_row["y"], "strFrame": pad_row["frame"],
            "direction": pad_row["direction"], "intState": 1,
            "apopID": npc["apop_id"], "Level": npc["level"],
            "intHP": hp, "intHPMax": hp,
        })
        if npc["name"]:
            mb["strMonName"] = npc["name"]
        if npc["subtitle"]:
            mb["strSubtitle"] = npc["subtitle"]
        if pad_row["requirement_data"]:
            mb["NPCRequirementData"] = pad_row["requirement_data"]
        mb.setdefault("equippedItems", {})
        # Equipped/humanoid NPCs are dressed from equippedItems + avatar customization (colours +
        # hair). Those live on the monster catalog, not the spawn monBranch — serve them here or the
        # client renders black skin / no hair. A per-pad colour (npc row) overrides the catalog.
        cat = montemplates.catalog(conn, mon_id) or {}
        for jk, col in (("SkinColor", "skin_color"), ("HairColor", "hair_color"),
                        ("EyeColor", "eye_color"), ("BaseColor", "base_color"),
                        ("TrimColor", "trim_color"), ("AccessoryColor", "accessory_color"),
                        ("HairID", "hair_id")):
            if npc[col]:
                mb[jk] = npc[col]
            elif cat.get(jk) is not None:
                mb[jk] = cat[jk]
        # Head slot: the client's Monster never sets showHelm (Monster ctor leaves it false), so
        # HelmLoader ignores the equipped Helm and always renders customization.HairBundle (forced
        # prefab "HelmGO"). We only sent HairID (which alone renders nothing) → no head piece. Feed
        # the equipped head item's (spot 3) bundle as HairBundle so it shows — the monster analogue
        # of a player's showHelm putting the helm over the hair.
        head = (mb.get("equippedItems") or {}).get("3") or (mb.get("equippedItems") or {}).get(3)
        if isinstance(head, dict) and head.get("Bundle"):
            mb["HairBundle"] = head["Bundle"]
            mb.setdefault("HairName", head.get("Name"))
        out.append(mb)
    return out


# ---- state / queries -------------------------------------------------------

def _state(conn, map_name):
    return conn.execute("SELECT * FROM map_state WHERE map=?", (map_name,)).fetchone()


def is_authored(conn, map_name):
    row = _state(conn, map_name)
    return bool(row and row["authored"])


def _pad_rows(conn, map_name):
    return conn.execute("SELECT * FROM map_pads WHERE map=? ORDER BY pad_id",
                        (map_name,)).fetchall()


def _npc_rows(conn, map_name, pad_id):
    return conn.execute(
        "SELECT * FROM pad_npcs WHERE map=? AND pad_id=? ORDER BY slot",
        (map_name, pad_id)).fetchall()


def take_over(conn, map_name, force=False):
    """Seed a map's pads from its captured monBranch and mark it authored.
    Idempotent: a no-op if already authored (unless force re-seeds)."""
    if is_authored(conn, map_name) and not force:
        return False
    area = _captured_area(map_name) or {}
    area_id = int(area.get("areaId", 0) or 0)
    if force:
        conn.execute("DELETE FROM map_pads WHERE map=?", (map_name,))  # cascades pad_npcs
    next_pad = NEW_PAD_BASE
    for mb in area.get("monBranch") or []:
        pad = _pad_from_monbranch(mb, area_id)
        write_pad(conn, map_name, pad)
        next_pad = max(next_pad, int(pad["ID"]) + 1)
    conn.execute(
        "INSERT INTO map_state(map, authored, next_pad_id, area_id) VALUES(?,1,?,?) "
        "ON CONFLICT(map) DO UPDATE SET authored=1, next_pad_id=excluded.next_pad_id, "
        "area_id=excluded.area_id",
        (map_name, max(NEW_PAD_BASE, next_pad), area_id))
    conn.commit()
    return True


def pad_dict(conn, map_name):
    """{pad_id: PadData} for the MapPadData response (seeds the editor)."""
    take_over(conn, map_name)
    return {pad["pad_id"]: _pad_dict(pad, _npc_rows(conn, map_name, pad["pad_id"]))
            for pad in _pad_rows(conn, map_name)}


def compiled_monbranch(conn, map_name):
    """The full monBranch compiled from a map's pads (authored maps only)."""
    out = []
    for pad in _pad_rows(conn, map_name):
        out.extend(_compile_pad(conn, pad, _npc_rows(conn, map_name, pad["pad_id"])))
    return out


def cell_entities(conn, map_name, frame):
    """CellJoin m:<MonMapID> entities for the authored monsters standing in a
    frame. The captured cells embed the *original* monster entities, so an
    authored map must rebuild them from the pad layer or removed NPCs reappear."""
    out = []
    for mb in compiled_monbranch(conn, map_name):
        if mb.get("strFrame") != frame:
            continue
        hp = mb.get("intHP") or mb.get("intHPMax") or 1
        out.append({
            "targetString": f"m:{mb['MonMapID']}",
            "x": mb.get("x", 0.0), "y": mb.get("y", 0.0),
            "HP": hp, "State": mb.get("intState", 1),
            "moveDirection": {}, "moveSpeed": 1.0,
            # identity the captured entities carry — lets the server scale monster damage by
            # level (P1-3) and (later) gate Dragon's Bane by race. Extra fields are ignored by
            # the client's CellJoin parser, and the real cells carried them anyway.
            "MonID": mb.get("MonID"), "intHPMax": mb.get("intHPMax") or hp,
            "Level": mb.get("Level", 1),
            "sRace": mb.get("sRace"), "strElement": mb.get("strElement"),
        })
    return out


def cell_monsters(conn, map_name, frame):
    """The compiled monBranch entries whose frame is `frame` — i.e. the monsters
    that belong in this cell. This drives CellJoin.monsterSpawns, which is what
    the client actually spawns NPCs from (ResponseCellJoin.Execute)."""
    frame = frame or "Enter"
    return [mb for mb in compiled_monbranch(conn, map_name)
            if (mb.get("strFrame") or "Enter") == frame]


def apply_to_area(conn, area, map_name):
    """If the map is authored, replace its monBranch with the compiled one.
    Mutates and returns `area`. No-op for vanilla maps."""
    if area is not None and is_authored(conn, map_name):
        area["monBranch"] = compiled_monbranch(conn, map_name)
    return area


# ---- editor mutations (one per c2s editor command) -------------------------

def save_pad(conn, map_name, pad_id, data_json):
    """SavePad [padID, PadDataJSON]: overwrite a pad from the editor's payload,
    keeping its current NPCs if the payload omits them."""
    take_over(conn, map_name)
    try:
        pad = json.loads(data_json)
    except Exception:
        return False
    pad_id = int(pad_id)
    pad["ID"] = pad_id
    if "NPCData" not in pad:
        pad["NPCData"] = [_npc_dict(r) for r in _npc_rows(conn, map_name, pad_id)]
    write_pad(conn, map_name, pad)
    conn.commit()
    return True


def add_mon(conn, map_name, mon_id, pad_id):
    """AddMon [monID, padID]: append an NPC (defaults from its template) to a pad."""
    take_over(conn, map_name)
    pad_id = int(pad_id)
    if conn.execute("SELECT 1 FROM map_pads WHERE map=? AND pad_id=?",
                    (map_name, pad_id)).fetchone() is None:
        return False
    slot = (conn.execute("SELECT COALESCE(MAX(slot), -1) FROM pad_npcs WHERE map=? AND pad_id=?",
                         (map_name, pad_id)).fetchone()[0]) + 1
    _write_npc(conn, map_name, pad_id, slot, _npc_from_template(conn, mon_id))
    conn.commit()
    return True


def add_new_pad(conn, map_name, pad_data_json):
    """AddNewPad [PadDataJSON]: create a pad at the given position; returns its id."""
    take_over(conn, map_name)
    try:
        incoming = json.loads(pad_data_json)
    except Exception:
        incoming = {}
    row = _state(conn, map_name)
    pad_id = int(row["next_pad_id"]) if row else NEW_PAD_BASE
    incoming["ID"] = pad_id
    incoming.setdefault("MapID", int(row["area_id"]) if row else 0)
    incoming.setdefault("NPCData", [])
    write_pad(conn, map_name, incoming)
    conn.execute("UPDATE map_state SET next_pad_id=? WHERE map=?", (pad_id + 1, map_name))
    conn.commit()
    return pad_id


def mon_delete(conn, map_name, mon_id, pad_id):
    """monDelete [mID, pID]: remove one NPC (by MonID) from a pad."""
    take_over(conn, map_name)
    cur = conn.execute("DELETE FROM pad_npcs WHERE map=? AND pad_id=? AND mon_id=?",
                       (map_name, int(pad_id), int(mon_id)))
    conn.commit()
    return cur.rowcount > 0


def pad_delete(conn, map_name, pad_id):
    """padDelete [pID]: remove a whole pad (cascades to its NPCs)."""
    take_over(conn, map_name)
    cur = conn.execute("DELETE FROM map_pads WHERE map=? AND pad_id=?",
                       (map_name, int(pad_id)))
    conn.commit()
    return cur.rowcount > 0
