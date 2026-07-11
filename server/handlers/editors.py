"""In-game authoring tools: the SkillForge (class/skill node-graph editor) and the
map NPC/pad editor. Both persist to our DB and are STAFF-gated centrally in
dispatch's STAFF_CMDS check — no per-handler gates here."""
import forge
import montemplates
import placements
import combat
import world

from .registry import register
from .context import send_obj, _base_area


# --- Skill Forge (class/skill node-graph editor; persists to our DB) ---
@register("sfInit")
async def sf_init(session, writer, cmd, params, msg):
    # FORGE opened: send palette + classes + skills
    init = forge.build_init(session.conn)
    await send_obj(writer, init)
    print(f"  [s2c] sfInit (classes={len(init['classes'])} "
          f"skills={len(init['skills'])} "
          f"nodes={sum(len(init[c]) for c in ('headers','nodes','helpers','conditionals','activators'))})")
    return


@register(*forge.MUTATIONS)
async def sf_mutation(session, writer, cmd, params, msg):
    # FORGE edit: persist + reply (sf*/sfError)
    resp = forge.handle_mutation(session.conn, cmd, params)
    if resp is not None:
        await send_obj(writer, resp)
        print(f"  [s2c] {resp.get('Cmd')} <- {cmd} {params}")
    return


# --- in-game NPC/pad editor (authoritative; persists to our DB) ---
@register("GetMapSpawns")
async def get_map_spawns(session, writer, cmd, params, msg):
    # Opening the NPC editor: seed pads from the captured monBranch (once),
    # then hand the client the pad dict it renders the editor from.
    pads = placements.pad_dict(session.conn, _base_area(session.area))
    await send_obj(writer, {"Cmd": "MapPadData",
                            "padData": {str(pid): pd for pid, pd in pads.items()}})
    print(f"  [s2c] MapPadData ({_base_area(session.area)}, {len(pads)} pads)")
    return


@register("getMonBranch")
async def get_mon_branch(session, writer, cmd, params, msg):
    # Params=[monID] -> monster template
    mon_id = params[0] if params else None
    tmpl = montemplates.get(session.conn, mon_id) if mon_id is not None else None
    if tmpl is not None:
        await send_obj(writer, {"Cmd": "getMonBranch", "template": tmpl})
        print(f"  [s2c] getMonBranch (MonID {mon_id})")
    return


@register("SavePad", "AddMon", "AddNewPad", "monDelete", "padDelete")
async def pad_edit(session, writer, cmd, params, msg):
    m = _base_area(session.area)
    if cmd == "SavePad" and len(params) >= 2:
        placements.save_pad(session.conn, m, params[0], params[1])
    elif cmd == "AddMon" and len(params) >= 2:
        placements.add_mon(session.conn, m, params[0], params[1])
    elif cmd == "AddNewPad" and params:
        placements.add_new_pad(session.conn, m, params[0])
    elif cmd == "monDelete" and len(params) >= 2:
        placements.mon_delete(session.conn, m, params[0], params[1])
    elif cmd == "padDelete" and params:
        placements.pad_delete(session.conn, m, params[0])
    n = len(placements.compiled_monbranch(session.conn, m))
    print(f"  [edit] {cmd} {params} -> {m}: {n} NPCs now (reload map to see)")
    return


# --- dev spawn tools: drop a live monster into the room (staff-gated) ---
@register("spawnMob", "spawnMapMob")
async def spawn_mob(session, writer, cmd, params, msg):
    # Params=[monID, x, y]. Build a transient monBranch entry, register it for combat/kill
    # resolution, and broadcast spawnMob {monBranch, x, y, reload} so every client spawns it.
    # RequestSpawnMob/SpawnMapMob -> ResponseSpawnMob.
    if session.member is None or len(params) < 3:
        return
    try:
        mon_id, x, y = int(params[0]), float(params[1]), float(params[2])
    except (ValueError, TypeError):
        return
    frame = session.member.frame or "Enter"
    mb = placements.single_monbranch(session.conn, mon_id, x, y, frame)
    # Learn its HP/identity/level so it's attackable and killable (kill-credit name too).
    combat.register_monster(session.area, f"m:{mb['MonMapID']}", mb.get("intHPMax"),
                            mon_id=mon_id, frame=frame, level=mb.get("Level"))
    moncat = combat._area_moncat.setdefault(session.area, {})
    moncat[mb["MonMapID"]] = (mon_id, mb.get("strMonName") or mb.get("Name") or "")
    reload = (cmd == "spawnMapMob")
    world.broadcast(session.area, {"Cmd": "spawnMob", "monBranch": mb,
                                   "x": x, "y": y, "reload": reload})
    print(f"  [spawn] {session.member.name} spawned mon {mon_id} @ {frame} ({x},{y})")
    return


@register("mapCapture")
async def map_capture(session, writer, cmd, params, msg):
    # Params=[geometryJson, footprintJson]. The map-geometry capture tool. We persist the raw
    # payload keyed by map so it's not lost; there's no s2c contract (client fires and forgets).
    if session.member is None or session.area is None or len(params) < 2:
        return
    m = _base_area(session.area)
    session.conn.execute(
        "INSERT INTO kv(k, v) VALUES(?, ?) ON CONFLICT(k) DO UPDATE SET v=excluded.v",
        (f"mapgeom:{m}", params[0]))
    session.conn.execute(
        "INSERT INTO kv(k, v) VALUES(?, ?) ON CONFLICT(k) DO UPDATE SET v=excluded.v",
        (f"mapfoot:{m}", params[1]))
    session.conn.commit()
    print(f"  [mapCapture] {session.member.name} saved geometry for {m}")
    return
