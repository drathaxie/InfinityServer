"""
Map service: serve the AreaJoin / CellJoin the client *requests*, from real
per-map data mined out of the capture (data/maps/<map>.json).

Wire contracts (from the decompiled client):
  tfer       Params = [userName, mapName, instance, frame, pad]   -> RequestMoveToArea
  moveToCell Params = [Frame, Pad, mapName]                       -> RequestMoveToCell

Each map file: {"area": <AreaJoin>, "cells": {"<Frame>": <CellJoin>, ...}}.
"""
import json

import placements

# Maps are served from the `maps` DB table (maps.doc holds the full {"area":<AreaJoin>,"cells":{...}}
# doc), the authoritative + editable source — R2 is gone. Docs are kept in an in-memory cache
# (_MAPS, keyed by the safe stem), warmed by load(conn) at startup and filled lazily on first
# request. Tests seed _MAPS directly (no DB needed for the cache path).
_MAPS = {}


def _safe(name):
    return "".join(ch if ch.isalnum() else "_" for ch in (name or "")).lower()


def _get(map_name, conn=None):
    """The cached map doc for a name, loading it from the maps table on first use (when a conn is
    given). None if we have no such map. Tests pre-seed _MAPS so the conn is optional."""
    key = _safe(map_name)
    if key in _MAPS:
        return _MAPS[key]
    if conn is None:
        return None
    row = conn.execute("SELECT doc FROM maps WHERE str_map_name=?", (key,)).fetchone()
    data = json.loads(row["doc"]) if row and row["doc"] else None
    if data is not None:
        _MAPS[key] = data
    return data


def load(conn):
    """Warm the in-memory cache from the maps table. Returns the number of maps cached."""
    _MAPS.clear()
    for row in conn.execute("SELECT str_map_name, doc FROM maps WHERE doc IS NOT NULL").fetchall():
        try:
            _MAPS[_safe(row["str_map_name"])] = json.loads(row["doc"])
        except Exception:
            continue
    return len(_MAPS)


def known(map_name, conn=None):
    return _get(map_name, conn) is not None


def list_maps():
    return sorted(_MAPS.keys())


def _clone(o):
    return json.loads(json.dumps(o))


def _strip_ghosts_area(area):
    """Remove the other players captured in this area instance.
    uoBranch = the user objects (Artix, etc.) present when we recorded it."""
    if isinstance(area.get("uoBranch"), list):
        area["uoBranch"] = []
    return area


def _strip_ghosts_cell(cell):
    """Remove other-player entities ('p:<uid>'); keep monsters/NPCs + spawns."""
    ents = cell.get("entities")
    if isinstance(ents, list):
        cell["entities"] = [
            e for e in ents
            if not str(e.get("targetString", "")).startswith("p:")
        ]
    return cell


def area_payload(map_name, conn=None):
    """The AreaJoin for a map (or None if we have no data for it).

    If a conn is given and the map has been 'taken over' in the editor, the
    captured monBranch is replaced with the one compiled from our authored pads
    (placements) — so removed NPCs stay gone and added ones appear.
    """
    m = _get(map_name, conn)
    if not (m and m.get("area")):
        return None
    area = _strip_ghosts_area(_clone(m["area"]))
    if conn is not None:
        placements.apply_to_area(conn, area, map_name)
    return area


def cell_payload(map_name, frame, pad="Spawn", conn=None):
    """CellJoin for (map, frame).

    If we captured this exact cell, serve it (ghosts stripped) so its real
    monster spawns appear. Otherwise SYNTHESIZE a minimal, valid CellJoin: the
    loaded .unity3d already contains every cell's geometry/pads, so the client
    just needs a move-to-cell ack to walk there.

    The captured cells embed the ORIGINAL monster entities (m:<MonMapID>), which
    is a *second* placement source besides AreaJoin's monBranch. So for a map
    we've taken over, replace those captured monsters with the authored set for
    this frame — otherwise removed NPCs reappear the moment you enter a cell.
    """
    m = _get(map_name, conn)
    cells = (m or {}).get("cells") or {}
    if frame and frame in cells:
        cell = _strip_ghosts_cell(_clone(cells[frame]))
    else:
        cell = {
            "Cmd": "CellJoin", "Frame": frame or "Enter", "Pad": pad or "Spawn",
            "entities": [], "monsterSpawns": [],
        }
    if conn is not None and placements.is_authored(conn, map_name):
        # monsterSpawns is what the client spawns NPCs from (ResponseCellJoin), so
        # it MUST be the authored set — replacing entities alone left them spawning.
        cell["monsterSpawns"] = placements.cell_monsters(conn, map_name, frame)
        cell["entities"] = [
            e for e in (cell.get("entities") or [])
            if not str(e.get("targetString", "")).startswith("m:")
        ] + placements.cell_entities(conn, map_name, frame)
    return cell
