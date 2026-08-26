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
        combat.cancel_player_actions(session.member.uid)  # real death flow (10s respawn timer)
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
        if combat.in_combat(session.member.uid, session.area):
            await send_obj(writer, {"Cmd": "rNotify", "msg": "You cannot rest while in combat."})
            return
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
    resource_before = combat.resource_total(session.member.uid)
    attack, hp, died = combat.monster_tile_hit(session.area, mon, session.member.uid, hits)
    if attack is None:                  # no armed skill for this monster (stale report)
        return
    await send_obj(writer, attack)
    world.broadcast(session.area, attack, exclude=session.member.uid)
    if combat.resource_total(session.member.uid) != resource_before:
        await send_obj(writer, combat.resource_packet(session.member.uid, session.member.name))
    print(f"        [mob] {mon} tile-hit {session.char['name']} x{hits} -> {hp} HP")
    if died:
        combat.cancel_player_actions(session.member.uid)
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
    resource_before = combat.resource_total(session.member.uid)
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
    if combat.resource_total(session.member.uid) != resource_before:
        await send_obj(writer, combat.resource_packet(
            session.member.uid, session.member.name))
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
    requested_target = combat.target_from_params(params)
    try:
        slot_i = int(slot)
    except ValueError:
        return
    uid = session.member.uid
    if slot_i < 0 or slot_i > 5:
        await send_obj(writer, combat.cast_failure(uid, slot_i, "Invalid skill slot."))
        return
    if combat.player_hp(uid) <= 0:
        await send_obj(writer, combat.cast_failure(
            uid, slot_i, "You cannot cast while defeated."))
        return
    # Slots 0-4 belong to the class. Slot 5 is the client's shipped sixth/potion slot and
    # resolves through the character's equipped ItemType 44 Spellstone instead.
    sk = (forge.equipped_spellstone(session.conn, session.char["id"])
          if slot_i == 5 and session.char is not None else
          forge.skill_for_slot(session.conn, session.equipped_class, slot_i))
    if sk is None:
        await send_obj(writer, combat.cast_failure(uid, slot_i, "Skill is unavailable."))
        return
    # Class skills are combat actions. A selected living monster always wins;
    # otherwise a running engagement wins, then the nearest living monster in
    # this cell. This lets a mobile player start combat by tapping a skill while
    # retaining explicit desktop target selection. Slot 5 remains usable out of
    # combat (potions, practice transformations, and other self-directed spellstones).
    target = requested_target
    if slot_i <= 4:
        target = combat.resolve_combat_target(
            session.area, uid, requested_target,
            frame=getattr(session.member, "frame", None),
            x=getattr(session.member, "x", None),
            y=getattr(session.member, "y", None))
        if target is None:
            await send_obj(writer, combat.cast_failure(
                uid, slot_i, "You need a living target to use combat skills."))
            return
    data = sk["data"] if sk else None
    forge_data = sk["forge"] if sk else None
    has_graph = sk is not None and combat.has_graph(data, forge_data)
    allowed, mana_required = combat.can_pay_resource(uid, slot_i, sk["skill_id"])
    if not allowed:
        await send_obj(writer, combat.cast_failure(
            uid, slot_i, f"Not enough Mana!,{mana_required}"))
        return
    cd = combat.skill_cooldown_ms(data, forge_data) if has_graph else 0
    if not combat.off_cooldown(uid, slot_i, cd):
        # A terminal Fail is required to release Combat.ExecutionState. Empty
        # Error avoids flashing a warning for a harmless duplicate request.
        await send_obj(writer, combat.cast_failure(uid, slot_i))
        return
    allies = _area_allies(session)      # heal/ally-buff targets (caster + nearby players)
    resource_before = combat.resource_total(uid)
    if slot_i == 0:
        # AUTO-ATTACK: single-shot (no igai handshake, so it re-fires cleanly) and
        # SERVER-SUSTAINED (the AI loop keeps swinging on the cooldown until the target
        # dies or you leave) so you don't have to spam it.
        auto_data = data if has_graph else None
        auto_forge = forge_data if has_graph else None
        auto_skill_id = sk["skill_id"] if has_graph else None
        pkts, killed, dmg = combat.execute_auto(
            session.area, uid, target, auto_data, auto_forge, auto_skill_id, allies)
        if pkts and pkts[0].get("StatusCode") == 1:
            combat.auto_engage(uid, session.area, target, auto_data, auto_forge,
                               cd or 600, auto_skill_id)
    elif has_graph:                     # authored skill: run its graph (handshake)
        pkts, killed, dmg = combat.begin_cast(session.area, uid, slot_i, target,
                                              data, forge_data, sk["skill_id"], allies)
        print(f"  [cast] slot {slot_i} ({sk['name']}) on {target}"
              f" -> {len(pkts)} pkt, {dmg} dmg{' KILL' if killed else ''}")
    else:                               # unauthored skill slot -> default hit
        pkts, killed, dmg = combat.begin_cast(session.area, uid, slot_i, target,
                                              data, forge_data, None, allies)
    cast_rejected = any(pk.get("Cmd") == "Attack" and pk.get("StatusCode") == 0
                        for pk in pkts)
    if cast_rejected:
        combat.release_cooldown(uid, slot_i)
    # A valid class cast may initially return only igai, or a pending Attack,
    # while the client resolves Range/Hitbox. Those accepted handshakes start
    # combat just as an immediate StatusCode-1 damage skill does.
    cast_accepted = bool(pkts) and not cast_rejected
    if 0 < slot_i <= 4 and cast_accepted and \
            not combat.auto_engaged(uid, session.area, target):
        # Starting combat with a class skill must also start the equipped class's
        # sustained slot-0 attack. Prime its cooldown so the first automatic swing
        # follows the authored cadence instead of overlapping the opening skill.
        auto_sk = forge.skill_for_slot(session.conn, session.equipped_class, 0)
        if auto_sk is not None:
            auto_data = auto_sk["data"]
            auto_forge = auto_sk["forge"]
            auto_has_graph = combat.has_graph(auto_data, auto_forge)
            auto_cd = (combat.skill_cooldown_ms(auto_data, auto_forge)
                       if auto_has_graph else 0) or 600
            repeat_data = auto_data if auto_has_graph else None
            repeat_forge = auto_forge if auto_has_graph else None
            repeat_skill_id = auto_sk["skill_id"] if auto_has_graph else None
            combat.off_cooldown(uid, 0, auto_cd)
            combat.auto_engage(uid, session.area, target, repeat_data, repeat_forge,
                               auto_cd, repeat_skill_id)
    for pk in pkts:
        await send_obj(writer, pk)
        world.broadcast(session.area, pk, exclude=session.member.uid)
        if pk.get("Cmd") == "Attack":
            summ = attack_summary(pk)
            if summ:
                print(f"        [combat] slot {slot_i}: {summ}")
    # The Attack Resource node is intentionally not trusted as the sole UI update:
    # handshakes and server-sustained autos can repaint a beat late. Re-sync every
    # model whenever the authoritative value changed (mana, determination, all stacks).
    if combat.resource_total(uid) != resource_before:
        await send_obj(writer, combat.resource_packet(uid, session.member.name))
    combat.engage(session.area, target, session.member.uid)   # monster now aggros
    await _handle_kills(session, writer, killed)
    return
