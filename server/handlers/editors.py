"""In-game authoring tools: the SkillForge (class/skill node-graph editor) and the
map NPC/pad editor. Both persist to our DB and are STAFF-gated centrally in
dispatch's STAFF_CMDS check — no per-handler gates here."""
import forge
import montemplates
import placements

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
