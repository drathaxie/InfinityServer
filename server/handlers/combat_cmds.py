"""Combat c2s: skill casts (gar) + input-node continuations (gai), monster tile-skill
reports (gmah/gmai), /kill + /die (tKill), rest/revive, and the recognized
charge-state no-ops."""
import combat
import forge
import world

from .registry import register
from .context import (send_obj, attack_summary, _area_allies, _gmah_hits,
                      _handle_kills)


@register("tKill")
async def tkill(session, writer, cmd, params, msg):
    # /kill (target monster) or /die (own p:uid)
    if session.char is None or session.member is None or not params:
        return
    ts = str(params[0])
    if ts.startswith("m:"):              # instakill the targeted monster -> normal rewards
        if int(session.char["access_level"] or 0) < 40:
            return                        # cheat: staff only
        if combat.kill_monster(session.area, ts):
            await _handle_kills(session, writer, [ts])
            print(f"  [kill] {session.member.name} -> {ts}")
    elif ts.startswith("p:"):            # /die -> lethal self-hit + entityDeath; the client runs
        combat.auto_disengage(session.member.uid)   # the real death flow (10s respawn timer)
        combat.drop_aggro_for(session.member.uid)
        hit = combat.lethal_self_packet(session.member.uid)
        death = combat.player_death_packet(session.member.uid, f"p:{session.member.uid}")
        for pk in (hit, death):
            await send_obj(writer, pk)
            world.broadcast(session.area, pk, exclude=session.member.uid)
        print(f"  [die] {session.member.name}")
    return


@register("rest", "resPlayerTimed")
async def rest_revive(session, writer, cmd, params, msg):
    if session.member is None or session.char is None:
        return
    if cmd == "rest":                       # out-of-combat rest -> full-heal HP (+ mana)
        pk = combat.rest_player(session.member.uid, session.char["name"])
        await send_obj(writer, pk)
        world.broadcast(session.area, pk, exclude=session.member.uid)
        print(f"  [s2c] hpmp (rest) uid={session.member.uid}")
    elif cmd == "resPlayerTimed":           # revive a downed player -> playerRes (+respawn)
        pk = combat.revive_player(session.member.uid, session.char["name"])
        await send_obj(writer, pk)
        world.broadcast(session.area, pk, exclude=session.member.uid)
        print(f"  [s2c] playerRes (revive) uid={session.member.uid}")
    return


@register("gmah")
async def mon_tile_hit(session, writer, cmd, params, msg):
    # RequestMonHit: a client reports whether its player was caught by a monster tile skill
    # (HitTiles/TileWave/... via Node.MonsterInput). Params=[monString, nodeName, *flag].
    # The flag (GeometryTileReport): absent -> 1 hit; "1" -> escaped (no damage); "0",N -> N
    # hits (TileCluster). We apply the damage server-side and broadcast the Attack, so the
    # red bars actually hurt. Damage + multiplier come from the armed skill (combat).
    if session.char is None or session.member is None or not params:
        return
    mon = params[0]
    hits = _gmah_hits(params)
    if hits <= 0:                       # player stood clear of the red — no damage
        return
    attack, hp, died = combat.monster_tile_hit(session.area, mon, session.member.uid, hits)
    if attack is None:                  # no armed skill for this monster (stale report)
        return
    await send_obj(writer, attack)
    world.broadcast(session.area, attack, exclude=session.member.uid)
    print(f"        [mob] {mon} tile-hit {session.char['name']} x{hits} -> {hp} HP")
    if died:
        combat.drop_aggro_for(session.member.uid)
        world.broadcast(session.area, combat.player_death_packet(session.member.uid, mon))
        print(f"  [death] {session.char['name']} slain by {mon} (tile, 10s respawn)")
    return


@register("gmai")
async def mon_req_continue(session, writer, cmd, params, msg):
    # RequestMonReq: client callback when a monster node's MonsterInput returns a chain.
    # Our tile skills are single-node (MonsterInput returns []), so there's nothing to
    # continue — recognized no-op (kept so it isn't logged as unhandled).
    return


@register("gai")
async def continue_cast(session, writer, cmd, params, msg):
    # client resolved an input node -> continue cast
    if session.member is None:
        return
    ctx = params[1] if len(params) > 1 else ""
    node_name = params[2] if len(params) > 2 else "?"
    pkts, killed, dmg = combat.resume_cast(ctx, params)
    for pk in pkts:
        await send_obj(writer, pk)
        world.broadcast(session.area, pk, exclude=session.member.uid)
        if pk.get("Cmd") == "Attack":
            summ = attack_summary(pk)
            if summ:
                print(f"        [combat] {node_name}: {summ}")
    if pkts:
        print(f"  [gai] {node_name} ctx={ctx} "
              f"-> {len(pkts)} pkt, {dmg} dmg{' KILL' if killed else ''}")
    await _handle_kills(session, writer, killed)
    return


@register("startCharge", "cancelCharge", "gas")
async def charge_state(session, writer, cmd, params, msg):
    # charge/attack-stream state is client-side; the release fires a gar. Ignore.
    return


@register("gar")
async def begin_cast(session, writer, cmd, params, msg):
    # begin a skill/auto-attack on a target
    if session.member is None:
        return
    slot = params[0] if params else "0"
    target = combat.target_from_params(params)
    try:
        slot_i = int(slot)
    except ValueError:
        return
    if slot_i == 0 and not target:
        return                          # auto-attack needs a target; self-buffs don't
    uid = session.member.uid
    sk = forge.skill_for_slot(session.conn, session.equipped_class, slot_i)
    data = sk["data"] if sk else None
    forge_data = sk["forge"] if sk else None
    has_graph = sk is not None and combat.has_graph(data, forge_data)
    cd = combat.skill_cooldown_ms(data, forge_data) if has_graph else 0
    if not combat.off_cooldown(uid, slot_i, cd):
        return                          # still cooling down -> ignore the cast
    allies = _area_allies(session)      # heal/ally-buff targets (caster + nearby players)
    if slot_i == 0:
        # AUTO-ATTACK: single-shot (no igai handshake, so it re-fires cleanly) and
        # SERVER-SUSTAINED (the AI loop keeps swinging on the cooldown until the target
        # dies or you leave) so you don't have to spam it.
        if has_graph and combat.class_rules(uid, sk["skill_id"]) is not None:
            # data-driven class: the auto is authored in the rule config too (the Infinity
            # Hero's Heroic Strike becomes the sky-blade once the pool is armed)
            pkts, killed, dmg = combat.begin_cast(session.area, uid, 0, target,
                                                  data, forge_data, sk["skill_id"], allies)
        elif has_graph:
            attack, killed, dmg = combat.cast_skill(session.area, uid, 0, target,
                                                    data, forge_data, sk["skill_id"], allies)
            pkts = [attack]
        else:
            attack, hit, _ = combat.auto_attack(session.area, target, uid)
            killed = [target] if hit else []
            pkts = [attack]
        combat.auto_engage(uid, session.area, target, data, forge_data, cd or 600)
    elif has_graph:                     # authored skill: run its graph (handshake)
        pkts, killed, dmg = combat.begin_cast(session.area, uid, slot_i, target,
                                              data, forge_data, sk["skill_id"], allies)
        print(f"  [cast] slot {slot_i} ({sk['name']}) on {target}"
              f" -> {len(pkts)} pkt, {dmg} dmg{' KILL' if killed else ''}")
    else:                               # unauthored skill slot -> default hit
        pkts, killed, dmg = combat.begin_cast(session.area, uid, slot_i, target,
                                              data, forge_data, None, allies)
    for pk in pkts:
        await send_obj(writer, pk)
        world.broadcast(session.area, pk, exclude=session.member.uid)
        if pk.get("Cmd") == "Attack":
            summ = attack_summary(pk)
            if summ:
                print(f"        [combat] slot {slot_i}: {summ}")
    # push a live resource re-sync so a consume (Smite empties Conviction) shows on the bar
    # THIS cast, not on the next one (the in-Attack Resource node repaints a beat late).
    if combat.resource_model(uid) in combat.STACK_MODELS:
        await send_obj(writer, combat.resource_packet(uid, session.member.name))
    combat.engage(session.area, target, session.member.uid)   # monster now aggros
    await _handle_kills(session, writer, killed)
    return
