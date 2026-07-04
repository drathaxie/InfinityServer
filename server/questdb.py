"""
Quest knowledge base for external tools (the Beyond bot): everything needed to run
a quest end-to-end without a human authoring chain entries.

Per objective it answers WHICH monsters credit it — mirroring game.record_kill's
precedence exactly (authored quest_objective_refs > numeric RefIDs > monster-name-
in-objective-name match) — and WHERE those monsters stand (map + frame from the
same monBranch the server actually serves: authored maps compile from the pad
layer via placements.compiled_monbranch, everything else from the captured
area.monBranch in data/maps/*.json). Item turn-ins (QOType 0) resolve to the
monsters whose monster_drops rows carry the item, plus a global-drop flag.

Served by webapi.py at GET data/questdb — unauthenticated, same zone as
data/getmonsterdata (it exposes only authored world content, nothing per-account).
Compiled on demand and cached for CACHE_TTL seconds so the bot polling it doesn't
rescan 40 maps per request.
"""
import json
import pathlib
import time

import db
import placements

MAPS_DIR = pathlib.Path(__file__).resolve().parent.parent / "data" / "maps"

# QuestObjectiveType (client enum): Turnin=0, Killcount=1, Interact=2, Talk=3,
# Apop=4, Cutscene=5. Keep in sync with game.QOT_* (not imported to avoid a cycle).
QOT_TURNIN, QOT_KILL, QOT_INTERACT, QOT_TALK, QOT_APOP, QOT_CUTSCENE = range(6)

CACHE_TTL = 60.0
_cache = {"at": 0.0, "data": None}


def _ref_ints(refids):
    return [int(t) for t in str(refids or "").split(",") if t.strip().lstrip("-").isdigit()]


def _captured_monbranch(map_name):
    p = MAPS_DIR / f"{map_name}.json"
    try:
        area = (json.loads(p.read_text(encoding="utf-8")) or {}).get("area") or {}
        return area.get("monBranch") or []
    except Exception:
        return []


def _all_map_names(conn):
    names = {p.stem for p in MAPS_DIR.glob("*.json")}
    try:
        names |= {r["map"] for r in conn.execute("SELECT map FROM map_state")}
    except Exception:
        pass
    return sorted(names)


def _placed_hostiles(conn):
    """Every HOSTILE monster placement the server would serve, across all maps.

    Returns (locations, mon_names, mon_levels):
      locations:  {mon_id: {(map, frame): count}}
      mon_names:  {mon_id: name}   (placed name wins — it's what record_kill sees)
      mon_levels: {mon_id: max placed level}
    """
    locations, mon_names, mon_levels = {}, {}, {}
    for map_name in _all_map_names(conn):
        if placements.is_authored(conn, map_name):
            mons = placements.compiled_monbranch(conn, map_name)
        else:
            mons = _captured_monbranch(map_name)
        for mb in mons:
            if int(mb.get("reactionType", 0) or 0) != 1:      # hostile only
                continue
            try:
                mon_id = int(mb.get("MonID") or mb.get("ID") or 0)
            except (TypeError, ValueError):
                continue
            if mon_id <= 0:
                continue
            frame = mb.get("strFrame") or "Enter"
            key = (map_name, frame)
            locations.setdefault(mon_id, {})
            locations[mon_id][key] = locations[mon_id].get(key, 0) + 1
            name = mb.get("strMonName") or ""
            if name:
                mon_names.setdefault(mon_id, name)
            lvl = int(mb.get("Level", 1) or 1)
            mon_levels[mon_id] = max(mon_levels.get(mon_id, 1), lvl)
    return locations, mon_names, mon_levels


def _kill_targets(conn, t, mon_names):
    """(mon_ids, via, drop_roll_or_None) for a Killcount objective — the same
    precedence record_kill applies on a live kill, so what the bot hunts is
    exactly what the server credits."""
    qoid = t.get("QOID")
    authored = db.objective_monsters(conn, qoid) if qoid is not None else set()
    if authored:
        chance, lo, hi = db.objective_drop(conn, qoid)
        roll = None if (chance >= 1.0 and lo == 1 and hi == 1) else \
            {"chance": chance, "min": lo, "max": hi}
        return sorted(authored), "authored", roll
    refs = _ref_ints(t.get("RefIDs"))
    if refs:
        return sorted(set(refs)), "refids", None
    oname = (t.get("Name") or "").lower()
    if oname:
        by_name = {mid for mid, n in mon_names.items() if n and n.lower() in oname}
        if by_name:
            return sorted(by_name), "name", None
    return [], "none", None


def _item_sources(conn, item_id):
    """Monsters whose drop table carries item_id, plus whether it drops globally."""
    mons = [{"monId": int(r["mon_id"]), "rate": float(r["rate"]),
             "quantity": int(r["quantity"])}
            for r in conn.execute(
                "SELECT mon_id, rate, quantity FROM monster_drops WHERE item_id=? "
                "ORDER BY rate DESC", (int(item_id),))]
    glob = conn.execute("SELECT rate FROM global_drops WHERE item_id=?",
                        (int(item_id),)).fetchone()
    return mons, bool(glob)


def _locs_for(mon_ids, locations, mon_levels):
    out = []
    for mid in mon_ids:
        for (map_name, frame), count in sorted((locations.get(mid) or {}).items()):
            out.append({"map": map_name, "frame": frame, "monId": mid,
                        "count": count, "level": mon_levels.get(mid, 1)})
    return out


def build(conn):
    """The full quest KB. Shape (consumed by Beyond's QuestDB.cs):
    { version, generatedAt, monsters: {id: {name, level}}, quests: {id: {...}} }"""
    locations, mon_names, mon_levels = _placed_hostiles(conn)
    quests = {}
    used_mons = set()
    for row in conn.execute("SELECT quest_id, raw FROM quests ORDER BY quest_id"):
        qid = int(row["quest_id"])
        try:
            raw = json.loads(row["raw"])
        except Exception:
            continue
        objectives, huntable, levels = [], True, []
        for t in db.quest_turnins(conn, qid):
            qot = int(t.get("QOType", 0) or 0)
            obj = {"qoid": t.get("QOID"), "type": qot,
                   "name": t.get("Name") or "", "qty": int(t.get("Quantity", 1) or 1),
                   "refIds": _ref_ints(t.get("RefIDs"))}
            if qot == QOT_KILL:
                mons, via, roll = _kill_targets(conn, t, mon_names)
                obj.update({"via": via, "monsters": mons,
                            "locations": _locs_for(mons, locations, mon_levels)})
                if roll:
                    obj["drop"] = roll
                used_mons.update(mons)
                levels += [mon_levels[m] for m in mons if m in mon_levels]
                if not obj["locations"]:
                    huntable = False
            elif qot == QOT_TURNIN:
                item_id = int(t.get("ItemID", -1) or -1)
                if item_id <= 0:
                    refs = _ref_ints(t.get("RefIDs"))
                    item_id = refs[0] if refs else -1
                mons, is_global = _item_sources(conn, item_id) if item_id > 0 else ([], False)
                mon_ids = [m["monId"] for m in mons]
                obj.update({"itemId": item_id, "via": "item-drops",
                            "sources": mons, "globalDrop": is_global,
                            "locations": _locs_for(mon_ids, locations, mon_levels)})
                used_mons.update(mon_ids)
                levels += [mon_levels[m] for m in mon_ids if m in mon_levels]
                if not obj["locations"] and not is_global:
                    huntable = False
            elif qot == QOT_INTERACT:
                # Map-machine click (a door/lever/armor piece). RefIDs are the
                # machine GameObject NAME(s) (strings, e.g. "FrontDoorOpen",
                # "DSPiece" covering DSPiece1..6) — the bot walks to the machine
                # in the quest's frame and clicks it. Machine placement lives in
                # the map bundle, not our DB, so we can't emit a location; the
                # quest's own frame is where it is. Still handleable.
                obj.update({"via": "interact",
                            "machines": [r for r in str(t.get("RefIDs") or "").split(",") if r.strip()]})
            else:
                # Apop/Cutscene/Talk: the refs let a bot complete Apop
                # (openApopQO) / Cutscene (watchCutscene) by request — surface
                # the type + refs and let the client decide.
                obj["via"] = "other"
                if qot not in (QOT_APOP, QOT_CUTSCENE):
                    huntable = False
            objectives.append(obj)
        quests[str(qid)] = {
            "id": qid, "name": raw.get("Name") or "",
            "once": bool(raw.get("Once")), "prevQuest": int(raw.get("prevQuest", -1) or -1),
            "map": raw.get("MapName") or "", "frame": raw.get("Frame") or "",
            "pad": raw.get("Pad") or "",
            "turnInMap": raw.get("TurnInMapName") or "",
            "turnInFrame": raw.get("TurnInFrame") or "",
            "turnInPad": raw.get("TurnInPad") or "",
            "objectives": objectives,
            # "huntable" = every objective is one the bot can drive: kills with a
            # known location, item turn-ins with a drop source, interact machines,
            # or apop/cutscene requests.
            "huntable": huntable and bool(objectives),
            "level": max(levels) if levels else 0,
        }
    monsters = {str(m): {"name": mon_names.get(m, ""), "level": mon_levels.get(m, 1)}
                for m in sorted(used_mons)}
    return {"version": 1, "generatedAt": int(time.time()),
            "monsters": monsters, "quests": quests}


def get(conn, qs=None):
    """webapi handler: GET data/questdb (cached; ?fresh=1 forces a rebuild)."""
    fresh = "fresh=1" in (qs or "")
    now = time.time()
    if not fresh and _cache["data"] is not None and now - _cache["at"] < CACHE_TTL:
        return _cache["data"]
    data = build(conn)
    _cache["at"], _cache["data"] = now, data
    return data
