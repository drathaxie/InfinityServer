"""World movement + map content: area joins (firstJoin/tfer), cell moves,
in-cell movement (mv), and NPC dialogue/cutscene serving (getApop/getDialog)."""
import combat
import game
import maps
import world

from .registry import register
from .context import send_obj, _enter_area


# --- generative, multiplayer world ---
@register("firstJoin", "tfer")
async def join_area(session, writer, cmd, params, msg):
    # Real instancing: "base-room". The base (display name -> lowercase) keys the map DATA;
    # the full "base-room" keys the INSTANCE (who-sees-whom, combat). The room comes from the
    # tfer's separate `instance` param (RequestMoveToArea Params=[user, map, INSTANCE, frame,
    # pad]) — or a "-N" suffix on the map name — and defaults to room 1, so a plain map join
    # groups everyone in <map>-1 while /join <map>-N enters a distinct room.
    if cmd == "firstJoin":
        base, room = "infinityportal", "1"
        await send_obj(writer, game.quest_data(session.conn, session.char))
    else:
        raw = ((params[1] if len(params) > 1 else session.area) or "infinityportal").lower()
        base = raw.split("-")[0]
        inst = params[2] if len(params) > 2 and str(params[2]).strip() else None
        room = inst or (raw.split("-", 1)[1] if "-" in raw else "1")
        await send_obj(writer, game.change_state(session.char))
    await _enter_area(session, writer, base, room)
    return


@register("moveToCell")
async def move_to_cell(session, writer, cmd, params, msg):
    # Params=[Frame, Pad, mapName]
    frame = params[0] if params else "Enter"
    pad = params[1] if len(params) > 1 else "Spawn"
    # the player stays in their current INSTANCE (session.area); the cell DATA comes from the
    # base map (room number stripped).
    base = (params[2] if len(params) > 2 and params[2] else session.area).split("-")[0].lower()
    await send_obj(writer, game.change_state(session.char))
    cell = maps.cell_payload(base, frame, pad, session.conn)  # authored-aware
    # learn each monster's HP + identity (id/frame/level/race) so HP bars stay sane, we
    # can re-spawn it (RespawnMon needs the catalog MonID + frame), and its swing scales
    # with level (P1-3). Keyed by the INSTANCE so each room's monsters are separate.
    for e in cell.get("entities", []) or []:
        ts = e.get("targetString", "")
        if ts.startswith("m:"):
            mon_id = e.get("MonID") or e.get("monID") or e.get("MonsterID")
            combat.register_monster(session.area, ts, e.get("HP"), mon_id=mon_id, frame=frame,
                                    level=e.get("Level") or e.get("intLevel"),
                                    race=e.get("sRace") or e.get("Race"),
                                    element=e.get("strElement") or e.get("Element"))
    if session.member is not None:
        # changing cell breaks combat: drop the player's aggro so monsters in the
        # old cell stop attacking them across frames, and stop auto-attacking.
        combat.drop_aggro_for(session.member.uid)
        combat.auto_disengage(session.member.uid)
        session.member.frame = frame
        peers = [world.entity(m) for m in world.members(session.area, exclude=session.member.uid)
                 if m.frame == frame]
        cell.setdefault("entities", [])
        cell["entities"].extend(peers)
    await send_obj(writer, cell)
    print(f"  [s2c] CellJoin ({session.area}/{frame})")
    return


@register("mv")
async def move(session, writer, cmd, params, msg):
    # Params=[posX, posY, dirX, dirY, ...]
    if session.member is not None and len(params) >= 4:
        try:
            px, py, dx, dy = (float(params[0]), float(params[1]),
                              float(params[2]), float(params[3]))
        except ValueError:
            return
        session.member.x, session.member.y = px, py
        world.broadcast(session.area, {
            "Cmd": "movement", "PlayerID": session.member.uid,
            "position": {"x": px, "y": py},
            "direction": {"x": dx, "y": dy}, "speed": 1.0,
        }, exclude=session.member.uid)
    return


@register("getApop")
async def get_apop(session, writer, cmd, params, msg):
    # Params=["id1,id2,..."] -> apop dialogs
    ids = []
    if params:
        for tok in str(params[0]).split(","):
            tok = tok.strip()
            if tok.lstrip("-").isdigit():
                ids.append(int(tok))
    apop_data = game.load_apops(session.conn, ids)
    await send_obj(writer, {"Cmd": "getApop", "apopData": apop_data})
    print(f"  [s2c] getApop ({len(apop_data)}/{len(ids)} served)")
    return


@register("getDialog")
async def get_dialog(session, writer, cmd, params, msg):
    # /cutscene <id> -> play a saved cutscene (Dialogger)
    try:
        did = int(params[0])
    except (IndexError, ValueError, TypeError):
        did = 0
    js = game.load_dialog(session.conn, did)
    await send_obj(writer, {"Cmd": "getDialog", "data": {"JsonText": js}})
    print(f"  [s2c] getDialog ({did}, {len(js)}B from cutscenes store)")
    return
