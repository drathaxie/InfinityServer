#!/usr/bin/env python3
"""
InfinityServer — a standalone private server for AQW Infinity.

Transport (reverse-engineered from Assembly-CSharp.dll, class AEC):
  - raw TCP; messages are UTF-8 JSON terminated by a single 0x00 byte
  - every message carries a "Cmd"; the client routes s2c via ResponseTypes

State: SQLite (db.py / game.py). No connection to Artix — accounts, characters,
gold, and inventory all live in the local database.

Login and the economy (buyItem/sellItem) are now authoritative and persistent.
The remaining cmds still replay genuine captured payloads (capture/samples)
until each is made generative in turn.
"""
import asyncio
import json
import pathlib
import sys
import time

import db
import seed
import game
import maps
import world
import combat
import placements
import montemplates
import forge
import patterns
import loot

HOST = "0.0.0.0"
PORT = 5588  # must match docs/RedirectPatch.cs

UNHANDLED_LOG = pathlib.Path(__file__).resolve().parent / "unhandled.jsonl"

# Client-side acks that need no s2c reply (movement confirms).
NOOP_CMDS = {"MoveOK", "mv"}

# STAFF-ONLY top-level cmds (access >= 40), gated centrally in dispatch. These read + rewrite
# authored content shared by every player: the SkillForge (class/skill graphs) and the in-game
# map pad editor (NPC placements). forge.MUTATIONS (the sf* edits) are folded in below.
STAFF_CMDS = {
    "sfInit", "GetMapSpawns", "getMonBranch",
    "SavePad", "AddMon", "AddNewPad", "monDelete", "padDelete",
} | set(forge.MUTATIONS)


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

    def close(self):
        try:
            self.conn.close()
        except Exception:
            pass


async def send_str(writer, payload_str):
    writer.write(payload_str.encode("utf-8") + b"\x00")
    await writer.drain()


async def send_obj(writer, obj):
    if obj is None:
        return
    await send_str(writer, json.dumps(obj, separators=(",", ":")))


def _base_area(area):
    """The base map name (room number stripped) for MAP-DATA lookups (pads, spawns) — instancing
    keeps the full 'base-room' in session.area, but authored map content is shared per base map."""
    return (area or "").split("-")[0].lower()


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
        area = world.leave(m)
        if area:
            world.broadcast(area, {"Cmd": "AreaRemove", "uid": m.uid, "unm": m.name})
    session.close()


def _is_staff(session, level=40):
    """Whether the logged-in character meets a staff access tier. The default 40 gates the
    authoring/edit commands (SkillForge mutations, the pad editor, /dbapop) SERVER-SIDE — the
    client only shows those tools at access 100, but a modded client can send the raw cmd, so
    the gate can't live in the UI alone."""
    return session.char is not None and int(session.char["access_level"] or 0) >= level


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

    if cmd == "cmd":                        # slash commands: Params=[name, args...]
        sub = params[0] if params else ""
        if sub == "dbapop" and len(params) > 1 and _is_staff(session):
            try:
                arg = int(params[1])
            except ValueError:
                return
            # The + button fires /dbapop <npcID> right after CreateNewApop, so when
            # arg matches the just-created apop's NPC we attach THAT new apop;
            # otherwise arg is an explicit apop id (manual assignment).
            kv = db.kv_get(session.conn, "last_created_apop")
            info = json.loads(kv) if kv else {}
            apop_id = info["apop_id"] if info and arg == info.get("npc_id") else arg
            pad_id = session.last_target
            if pad_id is None and info:    # fall back to the created apop's NPC
                row = session.conn.execute(
                    "SELECT pad_id FROM pad_npcs WHERE map=? AND mon_id=? "
                    "ORDER BY pad_id LIMIT 1", (_base_area(session.area), info.get("npc_id"))).fetchone()
                if row:
                    pad_id = row["pad_id"]
            if pad_id is not None:
                session.conn.execute("UPDATE pad_npcs SET apop_id=? WHERE map=? AND pad_id=?",
                                     (apop_id, _base_area(session.area), pad_id))
                session.conn.commit()
                print(f"  [dbapop] arg={arg} -> apop {apop_id} on {session.area} "
                      f"pad {pad_id} (reload map to see)")
            else:
                print(f"  [dbapop] arg={arg}: no target resolved")
            return
        if sub in ("modyell", "moderatoryell", "yell"):
            # server-wide gold announcement to EVERY connected player. Staff-only (access >= 40);
            # /modyell falls through RequestCmd so the client doesn't gate it — we do.
            if session.char is None or session.member is None:
                return
            if int(session.char["access_level"] or 0) < 40:
                await send_obj(writer, {"Cmd": "chatm", "msg": "You can not use that command.",
                                        "Name": "Server", "channel": "server", "ID": 0})
                return
            raw = " ".join(str(p) for p in params[1:]).strip()
            if not raw:
                return
            name, text = session.member.name, raw      # "spoofName@message" -> show as spoofName
            if "@" in raw:
                spoof, after = raw.split("@", 1)
                if spoof.strip() and after.strip():
                    name, text = spoof.strip(), after.strip()
            if text:
                world.broadcast_all({"Cmd": "chatm", "msg": text, "Name": name,
                                     "channel": "Adminyell", "ID": session.member.uid})
                print(f"  [modyell] {name} (by {session.member.name}): {text}")
            return
        if sub == "genderswap" and session.char is not None and session.member is not None:
            # Player gender swap (fired by an apop "chat" button, e.g. Bev). Flips M<->F, resets to
            # a gender-appropriate default hair, and replies ResponseGenderSwap so the client runs
            # SetGender + createAvatar. Free on our server (no coin cost). Broadcast so others see it.
            newg = "F" if (session.char["gender"] or "M").upper() == "M" else "M"
            hid = game._default_hair_id(session.conn, newg)
            session.conn.execute("UPDATE characters SET gender=?, hair_id=? WHERE id=?",
                                 (newg, hid, session.char["id"]))
            session.conn.commit()
            session.char = session.conn.execute("SELECT * FROM characters WHERE id=?",
                                                (session.char["id"],)).fetchone()
            hi = game._hair_info(session.conn, hid, newg) or {}
            pk = {"Cmd": "genderSwap", "success": True, "uid": session.member.uid, "gender": newg,
                  "HairID": hid, "strHairName": hi.get("Name") or "",
                  "coins": int(session.char["coins"] or 0)}
            await send_obj(writer, pk)
            world.broadcast(session.area, pk, exclude=session.member.uid)
            cust = session.member.user_obj.setdefault("customization", {})
            cust["HairID"] = hid
            print(f"  [genderswap] {session.char['name']} -> {newg} (hair {hid})")
            return
        # --- dev cheats (staff only, access >= 40) ---
        _staff = session.char is not None and int(session.char["access_level"] or 0) >= 40
        if sub == "addgold" and _staff:
            try:
                amt = int(params[1]) if len(params) > 1 else 0
            except (ValueError, TypeError):
                amt = 0
            if amt:
                old = int(session.char["gold"] or 0)
                new = max(0, old + amt)
                session.conn.execute("UPDATE characters SET gold=? WHERE id=?",
                                     (new, session.char["id"]))
                session.conn.commit()
                session.char = session.conn.execute("SELECT * FROM characters WHERE id=?",
                                                    (session.char["id"],)).fetchone()
                # addGoldXP adds Gold.val to the client's gold; send the ACTUAL delta. ExpTotal
                # must echo current exp or ResponseAddGoldXP zeroes the XP bar (Info.Exp=ExpTotal).
                await send_obj(writer, {"Cmd": "addGoldXP", "ExpTotal": int(session.char["exp"] or 0),
                                        "Gold": {"val": new - old}})
                print(f"  [addgold] {session.char['name']} {amt:+d} -> {new}")
            return
        if sub == "level" and _staff:
            try:
                lvl = max(1, min(int(params[1]), game.MAX_LEVEL))
            except (ValueError, TypeError, IndexError):
                return
            session.conn.execute("UPDATE characters SET level=?, exp=0 WHERE id=?",
                                 (lvl, session.char["id"]))
            session.conn.commit()
            session.char = session.conn.execute("SELECT * FROM characters WHERE id=?",
                                                (session.char["id"],)).fetchone()
            if session.member is not None:
                bonus = game.pattern_bonus(session.conn, session.char["id"])
                _sta, maxhp = game.build_combat_stats(session.char, bonus)
                combat.set_maxhp(session.member.uid, maxhp)
                await send_obj(writer, game.levelup_packet(session.char, lvl, 0, maxhp))
                await send_obj(writer, game.build_stat_update(session.char, hp=maxhp, bonus=bonus))
            print(f"  [level] {session.char['name']} -> {lvl}")
            return
        if sub == "item" and _staff:           # /item <itemID> [qty] -> load into inventory
            try:
                item_id = int(params[1])
                qty = int(params[2]) if len(params) > 2 else 1
            except (ValueError, TypeError, IndexError):
                return
            session.char = session.conn.execute(
                "SELECT * FROM characters WHERE id=?", (session.char["id"],)).fetchone()
            idef = db.item(session.conn, item_id)
            if idef is not None and patterns.is_gem_item(idef):
                # a gem item goes to the enhancement BAG (initPlayer.patterns[]), not the item
                # inventory — roll one per qty. New bag gems show after a relog (no live add-gem
                # packet is known), so tell the staffer.
                made = []
                for _ in range(max(1, qty)):
                    pat = patterns.gem_item_pattern(idef)
                    patterns.grant_gem(session.conn, session.char["id"], pat)
                    made.append(f"{pat['Name']} Q{pat['Quality']}")
                session.conn.commit()
                await send_obj(writer, {"Cmd": "chatm", "Name": "Server", "channel": "server",
                                        "ID": 0,
                                        "msg": f"+{len(made)} gem(s) to your bag ({', '.join(made)}). "
                                               f"Relog to see them in the enhancement menu."})
                print(f"  [item/gem] {session.char['name']} +{len(made)} bag gems: {made}")
                return
            if idef is not None and int(idef.get("EquipSpot", 0) or 0) in (
                    game.EQUIP_SPOT_HOUSE, game.EQUIP_SPOT_HOUSE_ITEM):
                # houses/furniture live in the houseItems list, not the bag — granting one
                # via addItems would wrongly insert it into the client's regular inventory
                # dict. Grant + tell the staffer (the house menu picks it up on relog).
                game.give_item(session.conn, session.char, item_id, qty)
                await send_obj(writer, {"Cmd": "chatm", "Name": "Server", "channel": "server",
                                        "ID": 0,
                                        "msg": f"+{qty} {idef.get('Name') or item_id} "
                                               f"(house item). Relog to see it in the house menu."})
                print(f"  [item/house] {session.char['name']} +{qty} of {item_id}")
                return
            item = game.give_item(session.conn, session.char, item_id, qty)
            if item is None:
                await send_obj(writer, {"Cmd": "chatm", "msg": f"No item {item_id} in catalog.",
                                        "Name": "Server", "channel": "server", "ID": 0})
                return
            # ResponseAddOrUpdateItems: shows the new/stacked item live, no relog needed.
            await send_obj(writer, {"Cmd": "addItems", "items": [item],
                                    "patternItems": [], "bankedItems": []})
            print(f"  [item] {session.char['name']} +{qty} of {item_id} "
                  f"(now {item['Quantity']})")
            return
        if sub == "gem" and _staff:            # /gem [archetype] [quality] -> gem every equipped gear
            arch, q = None, None                # e.g. "/gem warrior 8" -> Epic STR gems on all gear
            for p in params[1:3]:
                if str(p).isdigit():
                    q = int(p)
                elif str(p).lower() in patterns._ARCHETYPE_PRIMARY:
                    arch = str(p).lower()
            rows = session.conn.execute(
                "SELECT char_item_id, item_id FROM char_items "
                "WHERE char_id=? AND equipped=1", (session.char["id"],)).fetchall()
            applied = []
            for r in rows:
                idef = db.item(session.conn, r["item_id"])
                pat = patterns.roll_pattern(idef, archetype=arch, quality=q) if idef else None
                if pat is None:
                    continue                    # not enhanceable (materials/class items) -> skip
                session.conn.execute(
                    "UPDATE char_items SET pattern_json=?, char_pattern_id=char_item_id "
                    "WHERE char_item_id=?", (json.dumps(pat), r["char_item_id"]))
                applied.append(f"{pat['Name']} Q{pat['Quality']}/Base{pat['Base']}")
            session.conn.commit()
            su = _refresh_pattern_stats(session, as_statupdate=True)
            if su is not None:
                await send_obj(writer, su)      # push the new attack power / HP live
            note = "; ".join(applied) or "no enhanceable gear equipped"
            await send_obj(writer, {"Cmd": "chatm", "msg": f"Gemmed: {note}",
                                    "Name": "Server", "channel": "server", "ID": 0})
            print(f"  [gem] {session.char['name']} arch={arch} q={q} -> {applied}")
            return
        # other slash commands: log so we can see what the dev tools send
        print(f"        [cmd] '{sub}' {params[1:]} -> logged")
        try:
            with open(UNHANDLED_LOG, "a", encoding="utf-8") as fh:
                fh.write(json.dumps({"cmd": "cmd", "pkt": msg}) + "\n")
        except Exception:
            pass
        return

    # --- teleport: /goto (go to a player) + /summon (invite a player to you) ---
    if cmd == "GoToPlayer":                  # Params=[playerName] -> join their instance
        if session.char is None or session.member is None or not params:
            return
        target = world.find_member(params[0])
        if target is None or target.area is None:
            await send_obj(writer, {"Cmd": "chatm", "msg": f'"{params[0]}" is not online.',
                                    "Name": "Server", "channel": "server", "ID": 0})
            return
        if target.uid != session.member.uid:
            base = target.area.split("-")[0]
            room = target.area.split("-", 1)[1] if "-" in target.area else "1"
            await send_obj(writer, game.change_state(session.char))
            await _enter_area(session, writer, base, room)
            print(f"  [goto] {session.member.name} -> {target.name} @ {target.area}")
        return

    if cmd == "si":                          # summon invite: Params=[targetName]
        if session.member is None or not params:
            return
        target = world.find_member(params[0])
        if target is None:
            await send_obj(writer, {"Cmd": "chatm", "msg": f'"{params[0]}" is not online.',
                                    "Name": "Server", "channel": "server", "ID": 0})
            return
        if target.uid != session.member.uid:
            world.send(target, {"Cmd": "summonInvite", "summonerName": session.member.name,
                                "summonerID": session.member.uid})
            print(f"  [summon] {session.member.name} -> {target.name}")
        return

    if cmd in ("sa", "sd"):                   # summon accept / decline: Params=[summonerID]
        if session.char is None or session.member is None or not params:
            return
        try:
            summoner = world.find_uid(int(params[0]))
        except (ValueError, TypeError):
            return
        if cmd == "sa" and summoner is not None and summoner.area:
            base = summoner.area.split("-")[0]
            room = summoner.area.split("-", 1)[1] if "-" in summoner.area else "1"
            await send_obj(writer, game.change_state(session.char))
            await _enter_area(session, writer, base, room)
            print(f"  [summon-accept] {session.member.name} -> {summoner.name}")
        return

    if cmd == "tKill":                        # /kill (target monster) or /die (own p:uid)
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

    # --- authoritative, persistent flows ---
    if cmd == "Login":
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
        return

    if cmd in ("buyItem", "sellItem"):
        if session.char is None:
            return
        # refresh char row so gold reflects prior purchases this session
        session.char = session.conn.execute(
            "SELECT * FROM characters WHERE id=?", (session.char["id"],)
        ).fetchone()
        resp = (game.buy if cmd == "buyItem" else game.sell)(
            session.conn, session.char, params)
        await send_obj(writer, resp)
        # Live inventory: on a successful buy, also push the canonical add/update
        # packet (ResponseAddOrUpdateItems) so the new item shows without a relog.
        if cmd == "buyItem" and resp.get("Success") and resp.get("item"):
            await send_obj(writer, {"Cmd": "addItems", "items": [resp["item"]],
                                    "patternItems": [], "bankedItems": []})
        print(f"  [s2c] {cmd} (Success={resp.get('Success')})")
        return

    if cmd == "loadShop":
        await send_obj(writer, load_shop(session.conn, params))
        print("  [s2c] loadShop")
        return

    if cmd == "loadHairShop":                # HairShop apop button -> hair catalog (PUBLIC path to
        try:                                 # character customization; opens the customize overlay)
            shop_id = int(params[0]) if params else 0
        except (ValueError, TypeError):
            shop_id = 0
        resp = game.load_hairshop(session.conn, shop_id)
        await send_obj(writer, resp)
        print(f"  [s2c] loadHairShop ({shop_id}, {len(resp['hair'])} hairs)")
        return

    if cmd == "loadBank":                    # RequestLoadBank (no params) -> the char's banked items.
        if session.char is None:             # ResponseLoadBank.Cmd is "LoadBank" (capital B); items
            return                           # feed playerInventory.setupBank.
        items = game.bank(session.conn, session.char["id"])
        await send_obj(writer, {"Cmd": "LoadBank", "items": items})
        print(f"  [s2c] LoadBank ({len(items)} items)")
        return

    if cmd in ("bankFromInv", "bankToInv", "bankSwapInv"):
        # Bank moves (decomp: RequestInvToBank/BankToInv/BankSwap; Params = catalog item ids).
        # The client only mutates on the s2c reply, so a refused move (equipped / class item /
        # full / not owned) is answered by silence and nothing changes on either side.
        if session.char is None or not params:
            return
        session.char = session.conn.execute(
            "SELECT * FROM characters WHERE id=?", (session.char["id"],)).fetchone()
        if cmd == "bankFromInv":
            resp = game.bank_deposit(session.conn, session.char, params[0])
        elif cmd == "bankToInv":
            resp = game.bank_withdraw(session.conn, session.char, params[0])
        else:
            resp = game.bank_swap(session.conn, session.char, params[0],
                                  params[1] if len(params) > 1 else None)
        if resp is not None:
            await send_obj(writer, resp)
        print(f"  [s2c] {cmd} {params} -> {'ok' if resp else 'refused'}")
        return

    if cmd == "getQuests":                   # RequestGetQuests(Params=quest IDs) -> defs from catalog.
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

    if cmd == "qabandon":                    # RequestAbandonQuest -> drop an accepted quest
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
    if cmd in ("acceptQuest", "trackQuest", "openApopQO", "watchCutscene",
               "qobjective", "tryQuestComplete", "machineInteract"):
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
            await send_obj(writer, game.open_apop_qo(session.conn, session.char, arg))
            print(f"  [s2c] questData (openApopQO {arg})")
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

    # --- rest / revive / emote -----------------------------------------------
    if cmd in ("rest", "resPlayerTimed", "emotea"):
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
        elif cmd == "emotea":                   # echo emote to the WHOLE area, incl. the sender:
            pk = {"Cmd": "emotea", "userID": session.member.uid,   # the typed "/emote" path
                  "strEmote": params[0] if params else ""}         # (HandleEmote) has no local
            world.broadcast(session.area, pk)                      # playback and needs the echo.
            print(f"  [emotea] {pk['strEmote']}")
        return

    # --- saga reset / housing -------------------------------------------------
    if cmd == "resetsaga":                       # reset a quest storyline so it can be replayed
        if session.char is None:
            return
        await send_obj(writer, game.reset_saga(session.conn, session.char,
                                               params[0] if params else "0"))
        _refresh = session.conn.execute("SELECT * FROM characters WHERE id=?",
                                        (session.char["id"],)).fetchone()
        session.char = _refresh
        print(f"  [s2c] updateQuestBits (resetsaga {params[0] if params else ''})")
        return

    if cmd == "housesave":                       # persist a house layout (the save action works)
        if session.char is None:
            return
        await send_obj(writer, game.house_save(
            session.conn, session.char, params[0] if params else "0",
            params[1] if len(params) > 1 else "", params[2] if len(params) > 2 else "[]"))
        print(f"  [s2c] houseSave (map {params[0] if params else '?'})")
        return

    if cmd == "house":
        # Enter a house (RequestHouse: no params = your own; Params=[name] = visit an ONLINE
        # player's). A house is a normal AreaJoin carrying area.houseData (mapHouseData:
        # saved placements + the owner's furniture list + owner name) — the client builds the
        # map like any area and HouseItemManager places the furniture. Instanced per owner as
        # <houseMap>-<ownerUID>, matching AE's captured "house-508915". [[houses-doable]]
        if session.char is None or session.member is None:
            return
        owner_char = session.char
        if params and str(params[0]).strip():           # /house <name> -> visit
            target = world.find_member(str(params[0]).strip())
            tsess = _players.get(target.uid) if target is not None else None
            if tsess is None or tsess.char is None:
                await send_obj(writer, {"Cmd": "chatm", "msg": f'"{params[0]}" is not online.',
                                        "Name": "Server", "channel": "server", "ID": 0})
                return
            owner_char = tsess.char
        hid = game.equipped_house_id(session.conn, owner_char["id"])
        if hid <= 0:
            whose = "You don't" if owner_char["id"] == session.char["id"] else \
                f'"{owner_char["name"]}" doesn\'t'
            await send_obj(writer, {"Cmd": "chatm", "msg": f"{whose} have a house equipped.",
                                    "Name": "Server", "channel": "server", "ID": 0})
            return
        map_name = game.house_map_for(session.conn, hid)
        if maps.area_payload(map_name, session.conn) is None:
            # the deed's map isn't in our maps table (e.g. a house type we haven't captured):
            # tell the player instead of silently dumping them at the portal fallback.
            await send_obj(writer, {"Cmd": "chatm",
                                    "msg": f"That house's map ('{map_name}') isn't available yet.",
                                    "Name": "Server", "channel": "server", "ID": 0})
            return
        hd = game.build_house_data(session.conn, owner_char)
        await send_obj(writer, game.change_state(session.char))
        await _enter_area(session, writer, map_name, str(game.uid_for(owner_char)),
                          house_data=hd)
        print(f"  [house] {session.char['name']} -> {owner_char['name']}'s "
              f"{map_name} (deed {hid}, {len(hd['items'])} houseItems)")
        return

    if cmd == "gmah":
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

    if cmd == "gmai":
        # RequestMonReq: client callback when a monster node's MonsterInput returns a chain.
        # Our tile skills are single-node (MonsterInput returns []), so there's nothing to
        # continue — recognized no-op (kept so it isn't logged as unhandled).
        return

    if cmd == "removeItem":                 # Params=[itemID, qty] — delete from inventory
        if session.char is not None:
            session.char = session.conn.execute(
                "SELECT * FROM characters WHERE id=?", (session.char["id"],)).fetchone()
            cid = game.remove_item(session.conn, session.char, params)
            print(f"  [removeItem] {params} -> char_item {cid}")
        return

    # --- generative, multiplayer world ---
    if cmd in ("firstJoin", "tfer"):
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

    if cmd == "moveToCell":                 # Params=[Frame, Pad, mapName]
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

    if cmd == "gai":                        # client resolved an input node -> continue cast
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

    if cmd in ("startCharge", "cancelCharge", "gas"):  # charge/attack-stream state is
        return                              # client-side; the release fires a gar. Ignore.

    # --- gems / enhancements (patterns): empower an item so it can be equipped ---
    # Applying/removing a gem RECOMPUTES the player's stats (keystone): the equipped gems are
    # the source of the primary stats + the weapon's damage range. _refresh_pattern_stats pushes
    # the new attack power/weapon range into the combat engine and returns the recomputed `sta`
    # the UpdatePattern carries back (1=1: the capture's UpdatePattern.stats IS the stat refresh,
    # no separate statUpdate follows).
    if cmd == "itemdefaultpattern":         # "Power Up": mint+apply a default gem
        if session.char is not None and params:
            resp = patterns.item_default_pattern(session.conn, session.char["id"], params[0])
            if resp.get("Success"):
                resp["stats"] = _refresh_pattern_stats(session)
            await send_obj(writer, resp)
            print(f"  [s2c] UpdatePattern (item {params[0]} powered up, "
                  f"Success={resp.get('Success')})")
        return

    if cmd == "equipPattern":               # apply a chosen gem to an owned item
        if session.char is not None and len(params) >= 2:
            resp = patterns.equip_pattern(
                session.conn, session.char["id"], int(params[0]), int(params[1]),
                int(params[2]) if len(params) > 2 else -1)
            if resp.get("Success"):
                resp["stats"] = _refresh_pattern_stats(session)
            await send_obj(writer, resp)
            print(f"  [s2c] UpdatePattern (equipPattern {params})")
        return

    if cmd == "removePattern":              # clear a gem from an item
        if session.char is not None and params:
            resp = patterns.remove_pattern(session.conn, session.char["id"], int(params[0]))
            await send_obj(writer, resp)
            # removing a gem lowers the player's stats — refresh the HUD. (No removePattern
            # capture shows whether AE re-sends statUpdate here; this keeps stats honest.)
            su = _refresh_pattern_stats(session, as_statupdate=True)
            if su is not None:
                await send_obj(writer, su)
            print(f"  [s2c] removePattern ({params[0]})")
        return

    if cmd == "equipItem":                  # equipping a CLASS armor switches skills
        item_id = params[0] if params else None
        # a HOUSE deed equips via the house flow (ResponseEquipHouse sets EquippedHouseItemID
        # + flips the deed list's bEquip), never the avatar rig — the client sends the same
        # plain equipItem for both, so route by the item's EquipSpot here.
        if session.char is not None and item_id is not None:
            hresp = game.equip_house(session.conn, session.char, item_id)
            if hresp is not None:
                await send_obj(writer, hresp)
                print(f"  [s2c] equipHouse (deed {item_id})")
                return
        new_class = forge.class_for_armor_item(session.conn, item_id)
        if new_class is not None and session.char is not None:
            session.char = session.conn.execute(
                "SELECT * FROM characters WHERE id=?", (session.char["id"],)).fetchone()
            # actually EQUIP the class armor (sets char_items.equipped so the HUD shows the
            # class) + push the avatar update, then switch class skills.
            eq = game.equip_item(session.conn, session.char, item_id)
            if eq is not None:
                await send_obj(writer, eq)
                if session.member is not None:
                    world.broadcast(session.area, eq, exclude=session.member.uid)
                _refresh_pattern_stats(session)     # class armor's gem -> refresh combat power
            if new_class != session.equipped_class:
                session.equipped_class = new_class
                session.conn.execute("UPDATE characters SET class_id=? WHERE id=?",
                                     (new_class, session.char["id"]))
                session.conn.commit()
                session.char = session.conn.execute(
                    "SELECT * FROM characters WHERE id=?", (session.char["id"],)).fetchone()
                if session.member is not None:
                    # switch to the new class's resource model + send its real updateClass
                    # (DS white/orange-at-50; mana classes blue, no threshold)
                    uid = session.member.uid
                    res = forge.resource_for_class(session.conn, new_class)
                    combat.set_resource_model(uid, res["model"], res.get("MaxRP") or 100)
                    combat.set_class_mana(uid, forge.class_mana_costs(session.conn, new_class))
                    await send_obj(writer, forge.build_updateclass(session.conn, new_class, uid))
                await send_obj(writer, forge.build_seact(session.conn, new_class))
                name = session.conn.execute("SELECT name FROM classes WHERE class_id=?",
                                            (new_class,)).fetchone()
                print(f"  [equip] class armor {item_id} -> {name['name'] if name else new_class} "
                      f"(class {new_class}); equipped + skills switched")
            return
        # not a class armor: equip it (weapon/head/back/...) and push the avatar update
        if session.char is not None:
            session.char = session.conn.execute(
                "SELECT * FROM characters WHERE id=?", (session.char["id"],)).fetchone()
            resp = game.equip_item(session.conn, session.char, item_id)
            if resp is not None:
                await send_obj(writer, resp)
                world.broadcast(session.area, resp, exclude=session.member.uid
                                if session.member else None)
                # equipping a weapon/helm changes the active gem -> the damage range + stats
                # change. Refresh the combat engine + HUD (keystone: gems are the damage source).
                su = _refresh_pattern_stats(session, as_statupdate=True)
                if su is not None:
                    await send_obj(writer, su)
                print(f"  [equip] item {item_id} -> spot {resp['equipSpot']} (avatar updated)")
                return
        # unknown item: leave it for the unhandled log

    if cmd == "unequipItem":                # RequestUnequipItem -> Params=[itemID]
        if session.char is not None and session.member is not None and params:
            spot = game.unequip_item(session.conn, session.char, params[0])
            if spot is not None:            # None = required (Weapon/Class) or not equipped -> no-op
                session.char = session.conn.execute(
                    "SELECT * FROM characters WHERE id=?", (session.char["id"],)).fetchone()
                pk = {"Cmd": "unequipItem", "uid": session.member.uid,
                      "ItemID": int(params[0]), "ES": spot}
                await send_obj(writer, pk)
                world.broadcast(session.area, pk, exclude=session.member.uid)
                # removing a gemmed piece (e.g. a helm) changes the active gem -> refresh stats/HUD
                su = _refresh_pattern_stats(session, as_statupdate=True)
                if su is not None:
                    await send_obj(writer, su)
                print(f"  [unequip] item {params[0]} -> spot {spot} (avatar updated)")
        return

    if cmd == "gar":                        # begin a skill/auto-attack on a target
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
            if has_graph:
                attack, killed, dmg = combat.cast_skill(session.area, uid, 0, target,
                                                        data, forge_data, sk["skill_id"], allies)
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
        if combat.resource_model(uid) == "conviction":
            await send_obj(writer, combat.resource_packet(uid, session.member.name))
        combat.engage(session.area, target, session.member.uid)   # monster now aggros
        await _handle_kills(session, writer, killed)
        return

    if cmd == "mv":                         # Params=[posX, posY, dirX, dirY, ...]
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

    if cmd in ("message", "chat"):          # RequestChat -> Params = [msg, channel, target?]
        if session.member is not None and params:
            msg = params[0]
            channel = params[1] if len(params) > 1 else "zone"
            pk = {"Cmd": "chatm", "msg": msg, "Name": session.member.name,
                  "channel": channel, "ID": session.member.uid}
            if channel == "whisper" and len(params) > 2:
                target = world.find_member(params[2])
                # the client shows "To X:" locally; echo only to the recipient.
                if target is not None and target.uid != session.member.uid:
                    try:
                        target.writer.write(json.dumps(pk, separators=(",", ":")).encode() + b"\x00")
                    except Exception:
                        pass
                else:
                    await send_obj(writer, {"Cmd": "chatm", "msg": f'"{params[2]}" is not here.',
                                            "Name": "Server", "channel": "server", "ID": 0})
            elif channel in ("party", "guild"):
                # Party/guild membership isn't modelled yet, so broadcasting these to the physical
                # room would leak "private" chat to strangers who share the cell. Until membership
                # exists, echo back to the sender only — honest (nobody else is in your party/guild)
                # and non-leaky. [[party-guild-membership]]
                await send_obj(writer, pk)
            else:                            # zone (and any unknown channel) -> everyone in the room
                world.broadcast(session.area, pk)
            print(f"  [chatm/{channel}] {session.member.name}: {msg}")
        return

    if cmd == "getApop":                    # Params=["id1,id2,..."] -> apop dialogs
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

    if cmd == "getDialog":                  # /cutscene <id> -> play a saved cutscene (Dialogger)
        try:
            did = int(params[0])
        except (IndexError, ValueError, TypeError):
            did = 0
        js = game.load_dialog(session.conn, did)
        await send_obj(writer, {"Cmd": "getDialog", "data": {"JsonText": js}})
        print(f"  [s2c] getDialog ({did}, {len(js)}B from cutscenes store)")
        return

    # --- Skill Forge (class/skill node-graph editor; persists to our DB) — STAFF-gated in
    # dispatch's central STAFF_CMDS check above ---
    if cmd == "sfInit":                     # FORGE opened: send palette + classes + skills
        init = forge.build_init(session.conn)
        await send_obj(writer, init)
        print(f"  [s2c] sfInit (classes={len(init['classes'])} "
              f"skills={len(init['skills'])} "
              f"nodes={sum(len(init[c]) for c in ('headers','nodes','helpers','conditionals','activators'))})")
        return

    if cmd in forge.MUTATIONS:               # FORGE edit: persist + reply (sf*/sfError)
        resp = forge.handle_mutation(session.conn, cmd, params)
        if resp is not None:
            await send_obj(writer, resp)
            print(f"  [s2c] {resp.get('Cmd')} <- {cmd} {params}")
        return

    # --- /charedit : persist appearance (colours + hair), recolour avatars live ----
    if cmd == "changeColor":                 # Params=[Skin,Eye,Hair,Base,Trim,Accessory,HairID]
        if session.char is None or session.member is None:
            return
        applied = game.save_customization(session.conn, session.char, params)
        session.conn.commit()
        session.char = session.conn.execute(      # _refresh_char() is scoped to the quest block
            "SELECT * FROM characters WHERE id=?", (session.char["id"],)).fetchone()
        if applied:
            char = session.char
            try:                              # BaseColor has no column; pass it straight through
                base_color = int(params[3]) & 0xFFFFFF if len(params) > 3 else 0
            except (TypeError, ValueError):
                base_color = 0
            # ResponseChangeColor.Execute -> getPlayer(ID).ChangeColors(...). Broadcast to the
            # WHOLE area (incl. the editor) so every client recolours this player's avatar live.
            pk = {"Cmd": "changeColor", "ID": session.member.uid,
                  "SkinColor": char["skin_color"], "HairColor": char["hair_color"],
                  "EyeColor": char["eye_color"], "BaseColor": base_color,
                  "TrimColor": char["trim_color"], "AccessoryColor": char["accessory_color"],
                  "HairID": char["hair_id"]}
            # include the chosen hair's bundle so the client swaps the hairSTYLE live (matches AE,
            # which sends HairBundle in changeColor) — not just the hair colour.
            _hi = game._hair_info(session.conn, char["hair_id"], char["gender"])
            if _hi is not None:
                pk["HairBundle"] = _hi.get("Bundle")
            world.broadcast(session.area, pk)
            # keep the render object current so late-joiners (AreaAdd/uoBranch) see the new look.
            cust = session.member.user_obj.setdefault("customization", {})
            for jk, col in game._COLOR_COLS.items():
                cust[jk] = char[col]
            cust["HairID"] = char["hair_id"]
            print(f"  [s2c] changeColor (charedit) uid={session.member.uid} {applied}")
        return

    # --- in-game NPC/pad editor (authoritative; persists to our DB) — STAFF-gated in dispatch's
    # central STAFF_CMDS check above ---
    if cmd == "GetMapSpawns":
        # Opening the NPC editor: seed pads from the captured monBranch (once),
        # then hand the client the pad dict it renders the editor from.
        pads = placements.pad_dict(session.conn, _base_area(session.area))
        await send_obj(writer, {"Cmd": "MapPadData",
                                "padData": {str(pid): pd for pid, pd in pads.items()}})
        print(f"  [s2c] MapPadData ({_base_area(session.area)}, {len(pads)} pads)")
        return

    if cmd == "getMonBranch":               # Params=[monID] -> monster template
        mon_id = params[0] if params else None
        tmpl = montemplates.get(session.conn, mon_id) if mon_id is not None else None
        if tmpl is not None:
            await send_obj(writer, {"Cmd": "getMonBranch", "template": tmpl})
            print(f"  [s2c] getMonBranch (MonID {mon_id})")
        return

    if cmd in ("SavePad", "AddMon", "AddNewPad", "monDelete", "padDelete"):
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

    # --- loot / drops: keep pending drops out of the Loot Inventory window (capture) ---
    if cmd == "getDrop":                    # keep ONE drop -> addItems + getLoot
        if session.char is not None and session.member is not None and len(params) >= 2:
            add, got = loot.take(session.conn, session.char["id"], session.member.uid,
                                 params[0], params[1])
            if add is not None:
                await send_obj(writer, add)
            await send_obj(writer, got)
            print(f"  [s2c] getDrop item={params[0]} loot={params[1]} -> "
                  f"{'kept' if add else 'miss'}")
        return

    if cmd == "bulkOperation":              # keep ALL (IsLootAll) or discard all
        if session.char is not None and session.member is not None:
            if msg.get("IsLootAll"):
                add, bulk = loot.take_all(session.conn, session.char["id"], session.member.uid)
                await send_obj(writer, add)
                await send_obj(writer, bulk)
                print(f"  [s2c] bulkOperation loot-all -> {len(add['items'])} item(s)")
            else:
                await send_obj(writer, loot.discard_all(session.member.uid))
                print("  [s2c] bulkOperation discard-all")
        return

    # --- no-op acks / unhandled ---
    if cmd in NOOP_CMDS:
        return                                  # movement acks; nothing to send back
    print(f"        [unhandled] '{cmd}' -> logged to unhandled.jsonl")
    try:
        with open(UNHANDLED_LOG, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"cmd": cmd, "pkt": msg}) + "\n")
    except Exception:
        pass
    return


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


_mon_swing = {}             # (area, "m:ID") -> last swing time (AI loop pacing)
_players = {}               # uid -> Session (so the AI loop can reach a player's conn/writer)

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


async def ai_loop():
    """Autonomous monster AI: every aggro'd monster swings at its target on its own
    timer (MON_ATTACK_CD), independent of player input. Runs for the server's life."""
    while True:
        await asyncio.sleep(0.5)
        try:
            now = time.time()
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
                if now - _mon_swing.get((area, mon), 0.0) < combat.MON_ATTACK_CD:
                    continue
                _mon_swing[(area, mon)] = now
                attack, _hp, died = combat.monster_attack(area, mon, uid)
                world.broadcast(area, attack)
                print(f"        [mob] {mon} -> {attack_summary(attack)} (you {_hp} HP)")
                if died:
                    # the lethal Attack (Immediate, HP=0) + entityDeath makes the victim's client run
                    # its real death flow: Die -> 10s respawn UI -> resPlayerTimed -> playerRes. Don't
                    # auto-revive; just stop the monsters swinging at the corpse.
                    combat.drop_aggro_for(uid)
                    world.broadcast(area, combat.player_death_packet(uid, mon))
                    print(f"  [death] {mem.name} slain by {mon} (10s respawn)")

            # players keep auto-attacking their target until it dies or they leave
            for uid, area, target, data, fdata, cd in combat.auto_engagements():
                sess = _players.get(uid)
                if sess is None or sess.member is None or \
                        not any(m.uid == uid for m in world.members(area)):
                    combat.auto_disengage(uid)
                    continue
                if not combat.monster_alive(area, target):   # dead/respawning -> stop
                    combat.auto_disengage(uid)
                    continue
                if not combat.off_cooldown(uid, 0, cd):
                    continue
                if data is not None:
                    attack, killed, _dmg = combat.cast_skill(area, uid, 0, target, data, fdata)
                else:
                    attack, hit, _ = combat.auto_attack(area, target, uid)
                    killed = [target] if hit else []
                await send_obj(sess.writer, attack)          # the attacker
                world.broadcast(area, attack, exclude=uid)   # everyone else in the area
                if combat.resource_model(uid) == "conviction" and sess.member is not None:
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
