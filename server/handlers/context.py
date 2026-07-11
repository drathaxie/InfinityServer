"""Shared per-connection context + cross-domain helpers for the handler package.

Everything a handler needs that used to live at server.py module scope: the Session
class, the framed-JSON send helpers, the _players registry, and the game helpers
several domains share (_enter_area, _handle_kills, the gem stat refresh...).
Handler modules import THIS module — never server.py — which is what keeps the
package free of circular imports (server.py imports handlers, not the reverse).
"""
import asyncio
import json
import pathlib

import db
import game
import maps
import world
import combat
import placements
import forge
import loot

# server.py's directory (this file lives one level down in handlers/)
UNHANDLED_LOG = pathlib.Path(__file__).resolve().parent.parent / "unhandled.jsonl"

# Sentinel a handler returns to say "I didn't serve this after all" — dispatch()
# then falls through to the unhandled log, same as an unregistered cmd (the
# equipItem unknown-item path relies on this).
UNHANDLED = object()


class Session:
    """Per-connection state: DB handle, logged-in character, world membership."""
    def __init__(self, writer):
        self.conn = db.connect()
        self.writer = writer
        self.char = None
        self.area = "infinityportal"   # current map (home on first join)
        self.member = None             # world.Member once logged in
        self.last_target = None        # last m:<MonMapID> the client acted on (dev attach)
        self.equipped_class = forge.EQUIPPED_CLASS_ID   # whose skills are live (sEAct/combat)
        self.guildhall_gid = None      # guild id if currently inside a guild hall (routes housesave)

    def close(self):
        try:
            self.conn.close()
        except Exception:
            pass


_players = {}               # uid -> Session (so the AI loop can reach a player's conn/writer)


async def send_str(writer, payload_str):
    writer.write(payload_str.encode("utf-8") + b"\x00")
    await writer.drain()


async def send_obj(writer, obj):
    if obj is None:
        return
    await send_str(writer, json.dumps(obj, separators=(",", ":")))


def log_unhandled(cmd, msg):
    try:
        with open(UNHANDLED_LOG, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"cmd": cmd, "pkt": msg}) + "\n")
    except Exception:
        pass


def _base_area(area):
    """The base map name (room number stripped) for MAP-DATA lookups (pads, spawns) — instancing
    keeps the full 'base-room' in session.area, but authored map content is shared per base map."""
    return (area or "").split("-")[0].lower()


def _is_staff(session, level=40):
    """Whether the logged-in character meets a staff access tier. The default 40 gates the
    authoring/edit commands (SkillForge mutations, the pad editor, /dbapop) SERVER-SIDE — the
    client only shows those tools at access 100, but a modded client can send the raw cmd, so
    the gate can't live in the UI alone."""
    return session.char is not None and int(session.char["access_level"] or 0) >= level


_DT_NAME = {0: "", 1: "CRIT", 2: "DODGE", 3: "MISS", 5: "dot"}


def attack_summary(attack):
    """Compact combat narration of an Attack's nodes — so the server log tells the in-game
    story (heals, miss/dodge/crit, cleave, mana/RP, DoT). For the in-game confirm watch."""
    parts = []
    for n in attack.get("Nodes", []) or []:
        nm = n.get("Name")
        if nm == "Damage":
            for d, t, dt in zip(n.get("Damages") or [], n.get("Targets") or [],
                                n.get("DamageTypes") or []):
                if d < 0:
                    parts.append(f"HEAL+{-d}->{t}" + (" dot" if dt == 5 else ""))
                elif dt in (2, 3):
                    parts.append(f"{_DT_NAME[dt]}->{t}")
                else:
                    tag = "!" if dt == 1 else ("~dot" if dt == 5 else "")
                    parts.append(f"{d}{tag}->{t}")
        elif nm == "Resource":
            parts.append(f"[RP={n.get('Amount')}]")
        elif nm == "Aura":
            parts.append(f"<{n.get('AuraName')}>")
    return " ".join(parts)


def _area_allies(session):
    """Other players in the caster's current cell, as p:<uid> strings — the targets a heal
    or ally-buff resolves to (Healing Word heals you + up to 3 nearby allies). In a solo
    cell this is just the caster, which is the common captured case (a lone self-heal)."""
    if session.member is None:
        return []
    me = session.member
    return [f"p:{m.uid}" for m in world.members(session.area) if m.frame == me.frame]


def _refresh_pattern_stats(session, as_statupdate=False):
    """Recompute a player's combat stats from base attributes + their EQUIPPED gems (keystone)
    and push the new attack power / weapon range / max HP into the combat engine. Returns the
    recomputed wire `sta` (for UpdatePattern.stats), or — when as_statupdate — a full statUpdate
    packet (or None if there's no live player yet). Called whenever a gem is applied/removed."""
    if session.char is None:
        return None
    uid = game.uid_for(session.char)
    bonus = game.pattern_bonus(session.conn, session.char["id"])
    sta, maxhp = game.build_combat_stats(session.char, bonus)
    combat.set_power(uid, sta, weapon=bonus.get("weapon"))   # gems = the damage source
    combat.set_maxhp(uid, maxhp)                             # helm-gem HP, without healing
    if as_statupdate:
        return game.build_stat_update(session.char, hp=combat.player_hp(uid), bonus=bonus)
    return game.combat_sta(session.char, bonus)


def load_shop(conn, params):
    """Serve the requested shop live from the DB catalog (the authoritative store).

    A shop we don't have yet returns an honest *empty* shop for that id — never a
    masquerade of another shop. The client's ResponseLoadShop.Execute dereferences
    shop.mergeShop and Shop(shop) iterates shop.items with no null guards, so the
    response must be a valid shop object with items:[] (an empty window) — returning
    null or items:null would NRE the client."""
    shop_id = None
    if params:
        try:
            shop_id = int(params[-1])
        except ValueError:
            shop_id = None
    if shop_id is None:
        return None
    resp = game.load_shop(conn, shop_id)
    if resp is not None:
        return resp
    return {"Cmd": "loadShop",
            "shop": {"shopID": shop_id, "Name": "", "items": []}}


def _gmah_hits(params):
    """How many tile-skill hits a RequestMonHit (gmah) reports. Params=[mon, node, *flag];
    flag absent -> 1 hit, "1" -> escaped (0), "0",N -> N hits (TileCluster). See
    GeometryTileReport in the decomp."""
    rest = [str(p) for p in params[2:]]
    if not rest:
        return 1
    if rest[0] == "1":
        return 0
    if rest[0] == "0" and len(rest) > 1:
        try:
            return max(0, int(rest[1]))
        except (TypeError, ValueError):
            return 1
    return 1


async def _respawn_later(area, target):
    """After RESPAWN_DELAY, reset the dead monster and broadcast RespawnMon so it
    visually reappears for everyone in the area."""
    await asyncio.sleep(combat.RESPAWN_DELAY)
    world.broadcast(area, combat.respawn_packet(area, target))
    print(f"  [respawn] {target} in {area}")


async def _enter_area(session, writer, base, room, house_data=None):
    """Send the player into instance `base-room`: build the AreaJoin, move them in the world
    (announce departure/arrival), register that instance's monsters, and reply. Shared by
    firstJoin/tfer (client-initiated) and /goto + summon-accept (server-initiated).
    `house_data` (mapHouseData dict) makes the join a HOUSE: Area.isHouse keys on it."""
    area_name = f"{base}-{room}"
    session.guildhall_gid = None       # any area entry leaves a guild hall; the /guildhall handler re-sets it
    area = (maps.area_payload(base, session.conn)
            or maps.area_payload("infinityportal", session.conn))
    area["areaName"] = area_name               # tell the client which room it's in
    if house_data is not None:
        area["houseData"] = house_data         # placements + furniture + owner (Area.isHouse)
    else:
        # the captured house map docs EMBED the captured player's houseData (suswolf's 81
        # furniture items) — never serve that template on a plain map join (/join house):
        # without an owner's house_data the area is just a map, not anyone's house.
        area.pop("houseData", None)
    # learn this map's MonMapID -> (catalog MonID, name), keyed by the INSTANCE so each room
    # has its own monster set (the captured entities carry only the m:<MonMapID> instance id).
    combat.register_area_monsters(area_name, area.get("monBranch"))
    if session.member is not None:
        combat.drop_aggro_for(session.member.uid)   # changing map ends any combat
        combat.auto_disengage(session.member.uid)
        old = world.leave(session.member)            # leave old room, announce departure
        if old and old != area_name:
            world.broadcast(old, {"Cmd": "AreaRemove", "uid": session.member.uid,
                                  "unm": session.member.name})
        session.member.frame = "Enter"
        others = world.join(session.member, area_name)
        area["uoBranch"] = [m.user_obj for m in others]   # who's already in THIS room
    session.area = area_name
    await send_obj(writer, area)
    if session.member is not None:
        world.broadcast(area_name, {"Cmd": "AreaAdd", "userData": session.member.user_obj},
                        exclude=session.member.uid)
    print(f"  [s2c] AreaJoin ({area_name})  pop={len(world.members(area_name))}  "
          f"monBranch={len(area.get('monBranch') or [])}"
          f"{' [authored]' if placements.is_authored(session.conn, base) else ''}")


async def _handle_kills(session, writer, killed):
    """Per slain monster: entityDeath/mKill + a respawn timer; then one gold/XP reward
    batch (per kill) and a HUD stat refresh. Handles multi-kill (AoE) cleanly."""
    if not killed or session.char is None or session.member is None:
        return
    unique = list(dict.fromkeys(killed))        # dedupe if two nodes hit a dying target
    for target in unique:
        if combat.is_summoned_ts(target):       # summoned adds (clones) die for good — no respawn
            combat.forget_summon(session.area, target)
        else:
            asyncio.create_task(_respawn_later(session.area, target))
        for pk in combat.death_packets(session.area, target, session.member.uid):
            await send_obj(writer, pk)
            world.broadcast(session.area, pk, exclude=session.member.uid)
    row = session.conn.execute("SELECT * FROM characters WHERE id=?",
                               (session.char["id"],)).fetchone()
    gold_gain = combat.GOLD_PER_KILL * len(unique)
    exp_gain = combat.XP_PER_KILL * len(unique)
    session.conn.execute("UPDATE characters SET gold=? WHERE id=?",
                         (row["gold"] + gold_gain, row["id"]))
    level, new_exp, leveled = game.grant_xp(session.conn, session.char, exp_gain)
    session.conn.commit()
    session.char = session.conn.execute("SELECT * FROM characters WHERE id=?",
                                        (row["id"],)).fetchone()
    # rewardPlayer (replaces addGoldXP): currency + rolled DROPS held as pending loot the
    # player keeps/discards from the Loot Inventory (capture 2026-06-18).
    items_wire = []
    for target in unique:
        # roll THIS monster's drop table, keyed by its CATALOG MonID — NOT the m:<MonMapID>
        # instance id in the target string (monster_drops.mon_id is the catalog id).
        cat_id, _ = combat.monster_identity(session.area, target)
        drops = loot.roll_drops(session.conn, cat_id)
        items_wire += loot.add_pending(session.member.uid, drops)
    mon_id = unique[0].split(":", 1)[1] if ":" in unique[0] else 0
    await send_obj(writer, loot.reward_packet(mon_id, gold_gain, exp_gain, new_exp, items_wire))
    # carry the player's CURRENT HP so the post-kill stat refresh doesn't heal them to
    # full (P0-3) — killing a monster must not restore the player's HP bar. Fold the equipped
    # gems so MaxHP/stats stay consistent with login (keystone).
    bonus = game.pattern_bonus(session.conn, session.char["id"])
    if leveled:                                  # XP crossed a threshold -> level up
        _sta, maxhp = game.build_combat_stats(session.char, bonus)
        combat.set_maxhp(session.member.uid, maxhp)          # raise the HP cap (no auto-heal)
        lu = game.levelup_packet(session.char, level, new_exp, maxhp)
        await send_obj(writer, lu)
        world.broadcast(session.area, lu, exclude=session.member.uid)
        print(f"  [levelup] {session.char['name']} -> level {level}")
    await send_obj(writer, game.build_stat_update(
        session.char, hp=combat.player_hp(session.member.uid), bonus=bonus))
    print(f"  [kill] {unique} by uid={session.member.uid} "
          f"(+{gold_gain}g +{exp_gain}xp, {len(items_wire)} drop(s))")
    # credit Killcount quest objectives for each slain monster (server-driven; the client
    # doesn't send qobjective during normal play). Re-send questData if anything advanced.
    advanced = False
    for target in unique:
        # resolve the killed monster's catalog id + name (from the area monBranch) so kills
        # credit the RIGHT quest: RefID quests by id, RefID-less ones by objective-name match.
        mc, mname = combat.monster_identity(session.area, target)
        if game.record_kill(session.conn, session.char, mc, mname):
            advanced = True
    if advanced:
        await send_obj(writer, game.quest_data(session.conn, session.char))
