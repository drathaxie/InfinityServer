"""Quest defs + per-character quest progress (accept/track/objective/complete),
abandon, and saga reset."""
import game

from .registry import register
from .context import send_obj


@register("getQuests")
async def get_quests(session, writer, cmd, params, msg):
    # RequestGetQuests(Params=quest IDs) -> defs from catalog.
    ids = []
    for p in params:
        try:
            ids.append(int(p))
        except (TypeError, ValueError):
            pass
    await send_obj(writer, {"Cmd": "getQuests",
                            "quests": game.load_quests(session.conn, ids)})
    print(f"  [s2c] getQuests ({len(ids)} ids)")
    return


@register("qabandon")
async def abandon_quest(session, writer, cmd, params, msg):
    # RequestAbandonQuest -> drop an accepted quest
    if session.char is None or not params:
        return
    try:
        qid = int(params[0])
    except (ValueError, TypeError):
        return
    await send_obj(writer, game.abandon_quest(session.conn, session.char, qid))
    session.char = session.conn.execute("SELECT * FROM characters WHERE id=?",
                                        (session.char["id"],)).fetchone()
    print(f"  [qabandon] quest {qid}")
    return


# --- quest progress (per character, persisted) ---------------------------
@register("acceptQuest", "trackQuest", "openApopQO", "watchCutscene",
          "qobjective", "tryQuestComplete", "machineInteract")
async def quest_progress(session, writer, cmd, params, msg):
    if session.char is None:
        return
    try:
        arg = int(params[0])
    except (IndexError, ValueError, TypeError):
        return

    def _refresh_char():
        session.char = session.conn.execute(
            "SELECT * FROM characters WHERE id=?", (session.char["id"],)).fetchone()

    if cmd == "acceptQuest":
        await send_obj(writer, game.accept_quest(session.conn, session.char, arg))
        _refresh_char()
        await send_obj(writer, game.quest_data(session.conn, session.char))
        print(f"  [s2c] QuestAccept {arg}")
    elif cmd == "trackQuest":
        game.track_quest(session.conn, session.char, arg)
        _refresh_char()
    elif cmd == "openApopQO":
        # Params=[apopID, monMapID]. The client never opens the dialogue panel itself on this
        # path (unlike the direct NPC-click path, which calls HUDCanvas.LoadApop locally) — it
        # waits for a ResponseOpenApop {Cmd:"OpenApop"} to do so (see ResponseOpenApop.Execute()
        # in the decomp). Without it, the HUD quick-menu shortcuts (e.g. Gravelyn/Despair) silently
        # no-op: the server-side quest bookkeeping still ran, but nothing ever told the client to
        # render the panel.
        try:
            monmapid = int(params[1]) if len(params) > 1 else 0
        except (ValueError, TypeError):
            monmapid = 0
        await send_obj(writer, {"Cmd": "OpenApop", "ApopID": arg, "MonMapID": monmapid})
        await send_obj(writer, game.open_apop_qo(session.conn, session.char, arg))
        print(f"  [s2c] OpenApop + questData (openApopQO {arg})")
    elif cmd == "watchCutscene":
        await send_obj(writer, game.watch_cutscene(session.conn, session.char, arg))
        print(f"  [s2c] questData (watchCutscene {arg})")
    elif cmd == "qobjective":
        await send_obj(writer, game.quest_objective(session.conn, session.char, arg))
    elif cmd == "machineInteract":
        machine = params[1] if len(params) > 1 else ""
        await send_obj(writer, game.machine_interact(session.conn, session.char, arg, machine))
        print(f"  [s2c] questData (machineInteract {arg}/{machine})")
    elif cmd == "tryQuestComplete":
        choice = int(params[1]) if len(params) > 1 and str(params[1]).lstrip("-").isdigit() else -1
        resp = game.try_quest_complete(session.conn, session.char, arg, choice)
        reward_items = resp.pop("rewardItems", [])     # internal: push live so no relog needed
        await send_obj(writer, resp)
        if resp.get("Success"):
            _refresh_char()
            if reward_items:                            # show the reward in the bag immediately
                await send_obj(writer, {"Cmd": "addItems", "items": reward_items,
                                        "patternItems": [], "bankedItems": []})
            await send_obj(writer, game.quest_data(session.conn, session.char))
        print(f"  [s2c] QComp {arg} success={resp.get('Success')}")

    # After an objective completes, auto-turn-in any AutoTurnIn quest now ready (the
    # walk-to/talk-to/watch steps); kill quests are AutoTurnIn=false and turn in manually.
    if cmd in ("openApopQO", "watchCutscene", "machineInteract"):
        done = game.auto_turnin(session.conn, session.char)
        for qc in done:
            await send_obj(writer, qc)
            print(f"  [s2c] QComp {qc['ID']} (auto-turn-in)")
        if done:
            _refresh_char()
            await send_obj(writer, game.quest_data(session.conn, session.char))
    return


@register("resetsaga")
async def reset_saga(session, writer, cmd, params, msg):
    # reset a quest storyline so it can be replayed
    if session.char is None:
        return
    await send_obj(writer, game.reset_saga(session.conn, session.char,
                                           params[0] if params else "0"))
    _refresh = session.conn.execute("SELECT * FROM characters WHERE id=?",
                                    (session.char["id"],)).fetchone()
    session.char = _refresh
    print(f"  [s2c] updateQuestBits (resetsaga {params[0] if params else ''})")
    return
