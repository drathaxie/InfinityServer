#!/usr/bin/env python3
"""
Map service is DB-backed: maps.py serves the AreaJoin / CellJoin from the maps table (maps.doc
holds the full {"area":<AreaJoin>,"cells":{...}} doc) — the authoritative, editable source. No
CDN. Docs are cached in memory (_MAPS), warmed by load(conn) and filled lazily on first request.
"""
import json

import db
import maps

# A minimal but valid map doc (same shape as the served JSON: area=AreaJoin, cells map).
DOC = {"area": {"Cmd": "AreaJoin", "strMapName": "testtown", "DisplayName": "TestTown",
                "monBranch": [], "uoBranch": [{"x": 1}]},
       "cells": {"Enter": {"Cmd": "CellJoin", "Frame": "Enter", "Pad": "Spawn",
                            "entities": [{"targetString": "p:captured"},
                                         {"targetString": "m:42", "HP": 100}],
                            "monsterSpawns": [{"MonID": 42, "strFrame": "Enter"}]}}}


def main():
    db.use_throwaway()
    db.init()
    conn = db.connect()
    conn.execute("INSERT INTO maps(map_id, str_map_name, raw, doc) VALUES(?,?,?,?)",
                 (1, "testtown", "{}", json.dumps(DOC)))
    conn.commit()
    maps._MAPS.clear()

    # load() warms the in-memory cache from the maps table
    assert maps.load(conn) == 1 and "testtown" in maps._MAPS, "maps table warms the cache"
    assert maps.list_maps() == ["testtown"]

    # known/_get served from the in-memory cache
    assert maps.known("testtown") is True
    assert maps._get("testtown")["area"]["DisplayName"] == "TestTown"
    assert maps._get("testtown") is maps._MAPS["testtown"], "served from the in-memory cache"

    # lazy load from the DB on a cold cache (conn provided), then cached
    maps._MAPS.clear()
    assert maps._get("testtown", conn)["area"]["DisplayName"] == "TestTown", "lazy-loaded from the DB"
    assert "testtown" in maps._MAPS, "and then cached"

    # an unknown map is None (cold cache, no row) and is NOT cached (a map added later still resolves)
    maps._MAPS.clear()
    assert maps.known("ghosttown", conn) is False
    assert maps._get("ghosttown", conn) is None
    assert "ghosttown" not in maps._MAPS, "a miss must not be cached"

    # payloads come from the stored doc (ghosts stripped: uoBranch cleared)
    area = maps.area_payload("testtown", conn)
    assert area["DisplayName"] == "TestTown" and area["uoBranch"] == [], "AreaJoin from the DB, ghosts stripped"
    cell = maps.cell_payload("testtown", "Enter", conn=conn)
    assert cell["Cmd"] == "CellJoin" and cell["Frame"] == "Enter", "CellJoin from the DB"
    # A return route can land above the initial spawn, as on Asteroid's chest ledge.
    ledge = maps.cell_payload("testtown", "Enter", "Left", conn)
    assert ledge["Pad"] == "Left", "captured Spawn must not replace the requested destination pad"
    assert ledge["entities"] == [{"targetString": "m:42", "HP": 100}], "keep NPCs and strip captured players"
    assert ledge["monsterSpawns"] == DOC["cells"]["Enter"]["monsterSpawns"]
    assert maps._get("testtown")["cells"]["Enter"]["Pad"] == "Spawn", "route must not mutate the cached capture"
    assert maps.cell_payload("testtown", "Enter", "Right", conn)["Pad"] == "Right", "each arrival uses its own pad"
    assert maps.cell_payload("testtown", "Enter", conn=conn)["Pad"] == "Spawn", "initial arrival retains Spawn"
    assert maps.cell_payload("testtown", "Enter", "", conn)["Pad"] == "Spawn", "empty pad keeps the default"
    # an unknown frame synthesizes a minimal valid CellJoin
    uncaptured = maps.cell_payload("testtown", "Nowhere", "Left", conn)
    assert uncaptured["Frame"] == "Nowhere" and uncaptured["Pad"] == "Left"

    print("maps OK: DB-backed (maps.doc), load() warms cache, lazy DB load, miss not cached, "
          "AreaJoin/CellJoin served from the doc")
    print("ALL MAP TESTS PASSED")


if __name__ == "__main__":
    main()
