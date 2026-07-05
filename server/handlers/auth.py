"""Login + double-login eviction."""
import game
import world
import combat
import forge

from .registry import register
from .context import send_obj, _players


@register("Login")
async def login(session, writer, cmd, params, msg):
    username = params[1] if len(params) > 1 else "Hero"
    token = params[2] if len(params) > 2 else ""
    # The client presents the API-issued session token (account.sToken), NOT the password.
    # Validate it so a direct TCP connection can't log in as someone without authenticating.
    session.char = game.resolve_session(session.conn, username, token)
    if session.char is None:
        print(f"        [login] REJECTED {username} (no/invalid session token)")
        return
    # the live class = the character's saved class (falls back to Dragonslayer 1932)
    session.equipped_class = int(session.char["class_id"] or 0) or forge.EQUIPPED_CLASS_ID
    init = game.build_init_player(session.conn, session.char)
    uid = game.uid_for(session.char)
    session.member = world.Member(uid, session.char["name"], init.get("user", {}), writer)
    bonus = game.pattern_bonus(session.conn, session.char["id"])   # equipped-gem stats (keystone)
    sta, maxhp = game.build_combat_stats(session.char, bonus)
    combat.register_player(uid, maxhp)  # stat-derived HP (incl. gem HP); monsters can damage them
    combat.set_power(uid, sta, weapon=bonus.get("weapon"))  # gems = damage source
    # per-class resource model: DS builds Determination, others spend mana (P0-2)
    res = forge.resource_for_class(session.conn, session.equipped_class)
    combat.set_resource_model(uid, res["model"], res.get("MaxRP") or 100)
    combat.set_class_mana(uid, forge.class_mana_costs(session.conn, session.equipped_class))
    # Double-login / stale reconnect: a second live session on this character would clobber
    # the first (same uid keys _players, the world room, and combat), and the loser's later
    # disconnect would tear down the survivor. Evict the old session first — pull it from the
    # world so no stale room entry lingers, and close its socket.
    old = _players.get(uid)
    if old is not None and old is not session:
        if old.member is not None:
            oa = world.leave(old.member)
            if oa:
                world.broadcast(oa, {"Cmd": "AreaRemove", "uid": uid,
                                     "unm": old.member.name})
        try:
            old.writer.close()
        except Exception:
            pass
    _players[uid] = session             # so the AI loop can sustain auto-attack + reward
    print(f"        [login] {username} -> char#{session.char['id']} uid={uid} "
          f"gold={session.char['gold']} resource={res['model']}")
    await send_obj(writer, game.default_classes(session.conn))
    await send_obj(writer, init)
    print(f"  [s2c] initPlayer (generated, uid={uid})")
    await send_obj(writer, game.build_login_response(session.char))
    await send_obj(writer, game.build_stat_update(session.char, bonus=bonus))
    # the equipped class's real resource bar (DS white/orange-at-50; mana classes blue)
    await send_obj(writer, forge.build_updateclass(session.conn, session.equipped_class, uid))
    # sEAct (skill bar) AFTER updateClass — the class-swap path does this order and the HUD
    # only shows the right class's skills when the active class is set first; sEAct-before
    # -updateClass left the bar on Dragonslayer's skills until a manual class swap.
    await send_obj(writer, forge.build_seact(session.conn, session.equipped_class))
    print("  [s2c] loginResponse + statUpdate + updateClass + sEAct")
