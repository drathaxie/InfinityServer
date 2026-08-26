#!/usr/bin/env python3
"""
InfinityServer — a standalone private server for AQW Infinity.

Transport (reverse-engineered from Assembly-CSharp.dll, class AEC):
  - raw TCP; messages are UTF-8 JSON terminated by a single 0x00 byte
  - every message carries a "Cmd"; the client routes s2c via ResponseTypes

State: SQLite or Postgres (db.py / game.py). No connection to Artix — accounts,
characters, gold, and inventory all live in the local database.

Fully generative: every cmd is served live from the DB (no captured payloads are
replayed). dispatch() is the c2s command router: it parses the frame, applies the
central staff gate, and routes via the handlers/ package registry (one module per
domain; see handlers/__init__.py). game/combat/forge/world hold the actual logic.
"""
import asyncio
import json
import sys
import time

import db
import seed
import maps
import world
import combat
import montemplates
import forge
import loot
import parties
import guilds

import handlers
from handlers.context import (Session, send_str, send_obj, _players, _is_staff,  # noqa: F401
                              attack_summary, _handle_kills, _enter_area,        # noqa: F401
                              load_shop, _area_allies, UNHANDLED, log_unhandled) # noqa: F401
# (several of these are re-exported for the tests + any tooling that imports
# server.<name>; the handler modules themselves import handlers.context directly)

HOST = "0.0.0.0"
PORT = 5588  # must match docs/RedirectPatch.cs

# Client-side acks that need no s2c reply (movement confirms).
NOOP_CMDS = {"MoveOK", "mv"}

# STAFF-ONLY top-level cmds (access >= 40), gated centrally in dispatch. These read + rewrite
# authored content shared by every player: the SkillForge (class/skill graphs) and the in-game
# map pad editor (NPC placements). forge.MUTATIONS (the sf* edits) are folded in below.
STAFF_CMDS = {
    "sfInit", "GetMapSpawns", "getMonBranch",
    "SavePad", "AddMon", "AddNewPad", "monDelete", "padDelete",
    "spawnMob", "spawnMapMob", "mapCapture",
} | set(forge.MUTATIONS)


async def handle_client(reader, writer):
    peer = writer.get_extra_info("peername")
    print(f"[+] client connected: {peer}")
    session = Session(writer)
    buf = bytearray()
    try:
        while True:
            chunk = await reader.read(65536)
            if not chunk:
                break
            buf.extend(chunk)
            while b"\x00" in buf:
                idx = buf.index(0)
                raw = bytes(buf[:idx])
                del buf[: idx + 1]
                if raw:
                    await dispatch(session, writer, raw)
    except (ConnectionResetError, asyncio.IncompleteReadError):
        pass
    finally:
        cleanup_session(session)
        print(f"[-] client disconnected: {peer}")
        writer.close()


def cleanup_session(session):
    """Tear down a disconnecting session's shared world/combat state — but ONLY if it still
    owns its uid. The world room, _players and combat state are all keyed by uid, so a session
    that was SUPERSEDED by a double-login (see the Login handler) no longer owns _players[uid];
    running the full teardown for it would evict the live session that replaced it. The identity
    check (`_players.get(uid) is session`) makes the superseded session's cleanup a safe no-op."""
    m = session.member
    if m is not None and _players.get(m.uid) is session:
        _players.pop(m.uid, None)
        combat.forget_player(m.uid)   # stop monsters chasing a ghost
        loot.clear(m.uid)             # drop their un-kept pending loot
        parties.leave(m)              # pull them from any party (resyncs the rest)
        area = world.leave(m)
        if area:
            world.broadcast(area, {"Cmd": "AreaRemove", "uid": m.uid, "unm": m.name})
        # refresh online guildmates' rosters so this member flips to "Offline" for them (they see
        # it on their next guild-panel open — the client doesn't live-refresh that panel). Done
        # AFTER world.leave so guild_object recomputes this member as offline.
        gid = session.char["guild_id"] if session.char is not None else 0
        if gid:
            gobj = guilds.guild_object(session.conn, gid)
            if gobj is not None:
                guilds.broadcast(session.conn, gid, {"Cmd": "newGuild", "guild": gobj})
    session.close()


async def dispatch(session, writer, raw):
    try:
        msg = json.loads(raw.decode("utf-8", errors="replace"))
    except Exception:
        print(f"  [c2s] <unparseable {len(raw)}B>")
        return
    cmd = msg.get("Cmd") or msg.get("cmd") or "(none)"
    params = msg.get("Params") or []
    print(f"  [c2s] {cmd}")

    # Track the monster the client is acting on (m:<MonMapID>) so dev commands
    # like /dbapop can attach to "the targeted NPC" without an explicit target arg.
    for p in params:
        s = str(p)
        if s.startswith("m:") and s[2:].isdigit():
            session.last_target = int(s[2:])

    # STAFF GATE (one place, so the security posture is reviewable at a glance): these top-level
    # cmds read/write authored game content — the SkillForge and the map pad editor. The client
    # only exposes those tools at access 100, but a modded client can send the raw cmd, so the
    # gate lives here, not in the UI. (Slash-cmd cheats under "cmd" gate themselves on _is_staff
    # inside that handler, since they share the "cmd" envelope.)
    if cmd in STAFF_CMDS and not _is_staff(session):
        print(f"        [gate] REJECTED staff cmd {cmd} from uid="
              f"{session.member.uid if session.member else '?'}")
        return

    # route via the handler registry (handlers/: one module per domain). A handler may
    # return UNHANDLED to fall through to the log (equipItem's unknown-item path).
    fn = handlers.HANDLERS.get(cmd)
    if fn is not None:
        if await fn(session, writer, cmd, params, msg) is not UNHANDLED:
            return

    # --- no-op acks / unhandled ---
    if cmd in NOOP_CMDS:
        return                                  # movement acks; nothing to send back
    print(f"        [unhandled] '{cmd}' -> logged to unhandled.jsonl")
    log_unhandled(cmd, msg)
    return


_mon_swing = {}             # (area, "m:ID") -> last swing time (AI loop pacing)

# Monster tile-skill resolution for the AI loop. It uses a DEDICATED connection (never a player's
# session conn — that would leave it idle-in-transaction every tick, the hang pattern we fixed
# before) and a short cache so a live SkillForge edit still applies within a few seconds without
# hammering the DB on the 0.5s combat tick.
_ai_conn = None
_mon_skill_cache = {}       # mon_id -> (skills_list, expires_at)
_MON_SKILL_TTL = 10.0


def _monster_skills_for(mon_id, now):
    """The cached telegraphed tile skills for a monster (rotation list, possibly empty), resolved
    on a dedicated conn. Reconnects on error; never raises (a lookup failure just means 'no skills
    this tick')."""
    global _ai_conn
    hit = _mon_skill_cache.get(mon_id)
    if hit is not None and hit[1] > now:
        return hit[0]
    skills = []
    try:
        if _ai_conn is None:
            _ai_conn = db.connect()
        skills = forge.monster_skills(_ai_conn, mon_id)
        _ai_conn.commit()       # close the read txn so the conn never sits idle-in-transaction
    except Exception:
        try:
            if _ai_conn is not None:
                _ai_conn.close()
        except Exception:
            pass
        _ai_conn = None
    _mon_skill_cache[mon_id] = (skills, now + _MON_SKILL_TTL)
    return skills


def _clone_monbranch(mon_id, map_map_id, hp, level, frame, x, y):
    """Build the spawnMob monBranch for a summoned add: the clone's identity template (art/race/
    element from its catalog row) with the spawn-instance fields applied. Uses the dedicated AI
    conn; falls back to a minimal stub if the lookup fails (never raises)."""
    global _ai_conn
    mb = {}
    try:
        if _ai_conn is None:
            _ai_conn = db.connect()
        mb = dict(montemplates.template(_ai_conn, mon_id) or {})
        _ai_conn.commit()
    except Exception:
        try:
            if _ai_conn is not None:
                _ai_conn.close()
        except Exception:
            pass
        _ai_conn = None
    mb.update({"MonID": mon_id, "ID": mon_id, "MonMapID": map_map_id,
               "intHP": hp, "intHPMax": hp, "Level": level, "reactionType": 1, "intState": 1,
               "strFrame": frame or mb.get("strFrame") or "Enter",
               "x": x, "y": y, "fx": x, "fy": y})
    mb.setdefault("Scale", 1.0)
    mb.setdefault("apopID", -1)
    mb.setdefault("equippedItems", {})
    return mb


def _spawn_clones(area, boss_ts, uid, cfg):
    """Summon a boss's adds: register each in combat, broadcast spawnMob so clients render it, and
    aggro it onto the boss's target. Respects max_alive so a recurring summon can't flood. Returns
    how many were spawned this cast."""
    alive = combat.live_summon_count(area, boss_ts)
    to_spawn = max(0, min(cfg["count"], cfg["max_alive"] - alive))
    frame = next((m.frame for m in world.members(area) if m.uid == uid), "Enter")
    spawned = 0
    for i in range(to_spawn):
        x = cfg["x"] + (i * 3.0 - 1.5)            # fan multiple clones out so they don't overlap
        y = cfg["y"]
        clone_ts = combat.add_summon(area, boss_ts, cfg["mon_id"], cfg["hp"], cfg["level"],
                                     frame=frame)
        map_map_id = int(clone_ts.split(":")[1])
        mb = _clone_monbranch(cfg["mon_id"], map_map_id, cfg["hp"], cfg["level"], frame, x, y)
        world.broadcast(area, {"Cmd": "spawnMob", "monBranch": mb, "x": x, "y": y})
        combat.engage(area, clone_ts, uid)        # the add immediately fights the boss's target
        if cfg.get("self_break_ms"):              # Groglurk's Mirror: boss shatters it -> stun
            combat.arm_mirror_break(area, clone_ts, uid,
                                    cfg["self_break_ms"] / 1000.0, cfg.get("stun_secs") or 3.0)
        spawned += 1
    return spawned


async def _shatter_mirror(area, clone_ts, uid, stun_secs):
    """The boss breaks its own mirror: broadcast the mirror's death, then stun its target for
    stun_secs (client-enforced) and clear the stun when it expires. No kill credit/XP (the player
    didn't kill it). If the target already left, just drop the mirror."""
    for pk in combat.death_packets(area, clone_ts, uid):
        world.broadcast(area, pk)
    combat.forget_summon(area, clone_ts)
    sess = _players.get(uid)
    if sess is None or sess.member is None or sess.area != area:
        return
    for pk in combat.stun_packets(uid, caster=""):
        await send_obj(sess.writer, pk)
        world.broadcast(area, pk, exclude=uid)
    print(f"        [mob] mirror {clone_ts} shattered -> {stun_secs:.0f}s stun on uid={uid}")
    await asyncio.sleep(stun_secs)
    sess = _players.get(uid)                       # re-fetch: they may have left during the stun
    if sess is None or sess.member is None:
        return
    for pk in combat.unstun_packets(uid):
        await send_obj(sess.writer, pk)
        world.broadcast(sess.area, pk, exclude=uid)


async def ai_loop():
    """Autonomous monster AI: every aggro'd monster swings at its target on its own
    timer (MON_ATTACK_CD), independent of player input. Runs for the server's life."""
    cache_revisions = None
    next_cache_poll = 0.0
    while True:
        await asyncio.sleep(0.5)
        try:
            now = time.time()
            # The authoring web API is a separate process. It bumps DB revisions
            # after an apop/dialog/quest save; notify connected clients so their
            # new cacheReloaded handler refreshes the current area's live content.
            if now >= next_cache_poll:
                next_cache_poll = now + 1.0
                c = db.connect()
                try:
                    current_revisions = {
                        kind: db.kv_get(c, "cache_revision:" + kind, "")
                        for kind in ("apop", "dialog", "quest")
                    }
                finally:
                    c.close()
                if cache_revisions is not None:
                    for kind, revision in current_revisions.items():
                        if revision == cache_revisions.get(kind):
                            continue
                        packet = {"Cmd": "cacheReloaded", "rType": kind}
                        for sess in list(_players.values()):
                            if sess.member is not None:
                                await send_obj(sess.writer, packet)
                        print(f"  [cache] {kind} revision {revision} -> clients refreshed")
                cache_revisions = current_revisions

            # clear expired Dragon's Bane self-buffs — send an AuraChange remove so the red
            # glow doesn't linger forever (the cast applies the aura but never removed it).
            for uid in combat.expired_dragonbane():
                sess = _players.get(uid)
                if sess is not None and sess.member is not None:
                    pk = combat.aura_remove_packet(uid, "Dragonbane")
                    await send_obj(sess.writer, pk)
                    world.broadcast(sess.area, pk, exclude=uid)
                    print(f"  [aura] Dragonbane expired -> removed for uid={uid}")
            # Groglurk's Mirror: any mirror whose self-break timer elapsed (and the player didn't
            # kill it first) is shattered by the boss now, stunning its target.
            for area, clone_ts, uid, stun_secs in combat.due_mirror_breaks(now):
                asyncio.create_task(_shatter_mirror(area, clone_ts, uid, stun_secs))
            # Conviction (Paladin) drains when its owner stops fighting — push the shrinking
            # bar so the decay is visible (hpmp re-sync; HP untouched)
            for uid, total in combat.conviction_decay():
                sess = _players.get(uid)
                if sess is not None and sess.member is not None:
                    await send_obj(sess.writer, combat.resource_packet(uid, sess.member.name))
            # data-path casts can schedule a packet for LATER (the Infinity Hero meteor's
            # burning ground lands a second after the impact) — send those now that they're due
            for area, status, payload in combat.due_delayed(now):
                try:
                    from combat_engine import live as _engine_live
                    world.broadcast(area, _engine_live.delayed_attack(payload, status))
                except Exception as exc:                # never let a late packet kill the loop
                    print(f"  [rules] delayed packet dropped: {exc!r}")
            # monsters swing at the players they've aggro'd (unless stunned)
            for area, mon, uid in combat.engagements():
                mem = next((m for m in world.members(area) if m.uid == uid), None)
                if mem is None:                     # target left the area
                    combat.disengage(area, mon)
                    _mon_swing.pop((area, mon), None)
                    continue
                if combat.is_stunned(area, mon):    # Incapacitate etc. -> can't act
                    continue
                # Telegraphed tile skills (Ragnafluff's thin bars / scanning cross / firewalls):
                # rotate through the monster's class skills on each one's cooldown and broadcast a
                # MonReq per tile node — a single skill may fire several at once (the 4 firewalls
                # share a ReqTS). Damage lands later, when a caught client reports via gmah.
                # Resolved on a dedicated cached conn (see _monster_skills_for) so it can't stall
                # the combat tick.
                mon_id = combat.monster_catalog_id(area, mon)
                skills = _monster_skills_for(mon_id, now) if mon_id else []
                casted_skill = False
                if skills and combat.monster_skill_due(area, mon, now):
                    idx = (combat.monster_skill_index(area, mon) + 1) % len(skills)
                    skill = skills[idx]
                    combat.arm_monster_skill(area, mon, now, skill["cd_ms"] / 1000.0,
                                             skill.get("multiplier", 1.0), idx)
                    if "summon" in skill:
                        n = _spawn_clones(area, mon, uid, skill["summon"])
                        if n:
                            print(f"        [mob] {mon} casts {skill['name']} "
                                  f"(summon {n}x mon {skill['summon']['mon_id']})")
                    else:
                        req_ts = int(now * 1000)
                        for node in skill["nodes"]:
                            world.broadcast(area, {"Cmd": "MonReq", "TargetString": mon,
                                                   "ReqTS": req_ts, "Response": node})
                        print(f"        [mob] {mon} casts {skill['name']} "
                              f"({len(skill['nodes'])}x {skill['nodes'][0].get('Name')})")
                    casted_skill = True
                if casted_skill:
                    # A telegraphed/summon skill is this monster's action for the
                    # tick. Starting the melee timer here prevents a simultaneous
                    # invisible basic hit from landing underneath the telegraph.
                    _mon_swing[(area, mon)] = now
                    continue
                if now - _mon_swing.get((area, mon), 0.0) < \
                        combat.monster_attack_interval(area, mon):
                    continue
                _mon_swing[(area, mon)] = now
                resource_before = combat.resource_total(uid)
                attack, _hp, died = combat.monster_attack(area, mon, uid)
                world.broadcast(area, attack)
                if combat.resource_total(uid) != resource_before:
                    # Arcane Shield spends five mana per landed hit. The owning
                    # client needs an authoritative hpmp refresh immediately.
                    await send_obj(mem.writer, combat.resource_packet(uid, mem.name))
                print(f"        [mob] {mon} -> {attack_summary(attack)} (you {_hp} HP)")
                if died:
                    # the lethal Attack (Immediate, HP=0) + entityDeath makes the victim's client run
                    # its real death flow: Die -> 10s respawn UI -> resPlayerTimed -> playerRes. Don't
                    # auto-revive; just stop the monsters swinging at the corpse.
                    combat.cancel_player_actions(uid)
                    world.broadcast(area, combat.player_death_packet(uid, mon))
                    print(f"  [death] {mem.name} slain by {mon} (10s respawn)")

            # players keep auto-attacking their target until it dies or they leave
            for uid, area, target, data, fdata, cd, skill_id in combat.auto_engagements():
                sess = _players.get(uid)
                if sess is None or sess.member is None or \
                        not any(m.uid == uid for m in world.members(area)):
                    combat.auto_disengage(uid)
                    continue
                if combat.player_hp(uid) <= 0:
                    combat.cancel_player_actions(uid)
                    continue
                if not combat.monster_alive(area, target):   # dead/respawning -> stop
                    combat.auto_disengage(uid)
                    continue
                if not combat.off_cooldown(uid, 0, cd):
                    continue
                resource_before = combat.resource_total(uid)
                packets, killed, _dmg = combat.execute_auto(
                    area, uid, target, data, fdata, skill_id, _area_allies(sess))
                if packets and packets[0].get("StatusCode") == 0:
                    combat.auto_disengage(uid)
                for attack in packets:
                    await send_obj(sess.writer, attack)          # the attacker
                    if attack.get("StatusCode") != 0:
                        world.broadcast(area, attack, exclude=uid)  # successful casts are visible
                if combat.resource_total(uid) != resource_before:
                    await send_obj(sess.writer, combat.resource_packet(uid, sess.member.name))
                if killed:
                    await _handle_kills(sess, sess.writer, killed)
                    combat.auto_disengage(uid)               # target dead -> stop auto

            # DoT/HoT aura ticks (Bleeding/Scorched damage, Radiance heal) — P2-4
            for area, attack, killed in combat.aura_ticks():
                world.broadcast(area, attack)
                if killed:                                   # a DoT finished the monster off
                    caster = attack.get("Caster") or ""
                    cuid = int(caster[2:]) if caster.startswith("p:") and caster[2:].isdigit() else None
                    sess = _players.get(cuid)
                    if sess is not None:
                        await _handle_kills(sess, sess.writer, killed)
        except Exception as ex:                     # one bad tick must not kill the AI
            print(f"  [ai_loop] tick error: {ex}")


async def main():
    db.init()
    seed.run()
    _c = db.connect()                       # warm the map cache from the maps table
    print(f"[maps] {maps.load(_c)} maps loaded: {', '.join(maps.list_maps())}")
    _c.close()
    server = await asyncio.start_server(handle_client, HOST, PORT)
    addrs = ", ".join(str(s.getsockname()) for s in server.sockets)
    store = "Postgres" if db.BACKEND == "postgres" else f"SQLite: {db.DB_PATH}"
    print(f"InfinityServer listening on {addrs}  ({store})")
    asyncio.create_task(ai_loop())          # autonomous monster combat
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nbye")
        sys.exit(0)
