"""The "cmd" envelope: slash commands. Dev cheats (/addgold /level /item /gem),
/dbapop attachment, /genderswap, and the /modyell delegate (social.modyell).

The slash cmds gate themselves on _is_staff inside this handler (they all share
the "cmd" envelope, so the central STAFF_CMDS gate in dispatch can't split them)."""
import asyncio
import json

import db
import game
import world
import combat
import patterns

from .registry import register
from .context import (send_obj, _base_area, _is_staff, _refresh_pattern_stats,
                      load_shop, log_unhandled)
from . import social
from . import guild_cmds


@register("cmd")
async def slash_cmd(session, writer, cmd, params, msg):   # slash commands: Params=[name, args...]
    # Some client paths send Params=["shop 1"] instead of ["shop", "1"].
    params = [tok for part in (params or []) for tok in str(part).strip().split()]
    sub = (params[0].lstrip("/").lower() if params else "")

    # Batch statue generation via the REAL render pipeline: summon each character's assembled
    # avatar into THIS dev's client (AreaAdd — same path other players use), tell the mod to run it
    # through the statue capture (stone-grade + pedestal + upload), then remove it. `/genstatue <id>`
    # does one; `/genstatues` does the whole roster. Only this client sees the summoned avatars.
    if sub in ("genstatue", "genstatues") and _is_staff(session):
        conn = session.conn
        my_id = int(session.char["id"]) if session.char is not None else 0
        if sub == "genstatue" and len(params) > 1:
            try:
                ids = [int(params[1])]
            except ValueError:
                ids = []
        else:
            ids = [r["id"] for r in conn.execute(
                "SELECT id FROM characters ORDER BY id").fetchall() if int(r["id"]) != my_id]

        async def _say(m):
            await send_obj(writer, {"Cmd": "chatm", "Name": "Server", "channel": "server",
                                    "ID": 0, "msg": m})

        await _say(f"Generating {len(ids)} statue(s) via the render pipeline — stay in this map.")
        done = 0
        for cid in ids:
            char = conn.execute("SELECT * FROM characters WHERE id=?", (cid,)).fetchone()
            if char is None:
                continue
            try:
                user = game.build_init_player(conn, char)["user"]
            except Exception as ex:
                print(f"  [genstatue] build failed cid={cid}: {ex}")
                continue
            uid, name = user.get("uid"), user.get("Name")
            await send_obj(writer, {"Cmd": "AreaAdd", "userData": user})
            await asyncio.sleep(4.0)                       # client loads the avatar's item bundles
            await send_obj(writer, {"Cmd": "captureStatue", "cid": int(cid), "name": name})
            await asyncio.sleep(3.0)                       # mod renders + uploads
            await send_obj(writer, {"Cmd": "AreaRemove", "uid": uid, "unm": name})
            await asyncio.sleep(0.4)
            done += 1
        await _say(f"Statue generation complete ({done} rendered).")
        return

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
        await social.modyell(session, writer, params)
        return
    if sub in ("shop", "devshop", "wikishop", "wikiitems"):
        if not _is_staff(session):
            await send_obj(writer, {"Cmd": "chatm", "Name": "Server", "channel": "server",
                                    "ID": 0, "msg": "You can not use that command."})
            return
        try:
            arg = int(params[1]) if len(params) > 1 else None
        except (ValueError, TypeError):
            arg = None
        if sub in ("wikishop", "wikiitems"):
            page = max(1, arg or 1)
            shop_id = 89891 + page - 1
        else:
            shop_id = arg or 89891
        resp = load_shop(session.conn, [shop_id], is_staff=True)
        await send_obj(writer, resp)
        print(f"  [s2c] /{sub} -> loadShop {shop_id}")
        return
    # guild extras that have no native client command -> arrive here via the `cmd` envelope.
    if sub in ("gleave", "guildleave", "gquit"):
        await guild_cmds.guild_leave(session, writer, params[1:])
        return
    if sub in ("guildhall", "gh"):
        await guild_cmds.guild_hall(session, writer, params[1:])
        return
    if sub in ("tagcolor", "tagcolour", "tagcolors", "guildcolor", "gtagcolor"):
        await send_obj(writer, {"Cmd": "chatm", "Name": "Server", "channel": "server",
                                "ID": 0, "msg": "Custom guild nameplate tags are no longer available."})
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
    if sub == "addcoin" and _staff:
        # /addcoin n -> grant AdventureCoins (the `coins` column). ResponseUpgradeSync
        # does Info.SetCoins(Coins) — an ABSOLUTE set — so echo the new TOTAL, not a delta
        # (unlike /addgold, which adds Gold.val). Negative n subtracts (clamped at 0).
        try:
            amt = int(params[1]) if len(params) > 1 else 0
        except (ValueError, TypeError):
            amt = 0
        if amt:
            old = int(session.char["coins"] or 0)
            new = max(0, old + amt)
            session.conn.execute("UPDATE characters SET coins=? WHERE id=?",
                                 (new, session.char["id"]))
            session.conn.commit()
            session.char = session.conn.execute("SELECT * FROM characters WHERE id=?",
                                                (session.char["id"],)).fetchone()
            await send_obj(writer, {"Cmd": "upgradeSync", "Coins": new,
                                    "UpgradeDays": game.membership_days(session.char),
                                    "UpgradeExpires": game.membership_expires(session.char)})
            print(f"  [addcoin] {session.char['name']} {amt:+d} -> {new}")
        return
    if sub in ("member", "membership", "upg", "upgrade") and _staff:
        # /member [days] grants yourself membership days.
        # /member <name> <days> grants another character, online or offline.
        target_name = session.char["name"]
        raw_days = params[1] if len(params) > 1 else "30"
        if len(params) > 2:
            target_name, raw_days = params[1], params[2]
        try:
            days = max(0, int(raw_days))
        except (ValueError, TypeError):
            return
        target = session.conn.execute("SELECT * FROM characters WHERE LOWER(name)=LOWER(?)",
                                      (target_name,)).fetchone()
        if target is None:
            await send_obj(writer, {"Cmd": "chatm", "Name": "Server", "channel": "server",
                                    "ID": 0, "msg": f"No character named {target_name}."})
            return
        days, expires = game.set_membership(session.conn, target["id"], days)
        session.conn.commit()
        if target["id"] == session.char["id"]:
            session.char = session.conn.execute("SELECT * FROM characters WHERE id=?",
                                                (session.char["id"],)).fetchone()
            if session.member is not None:
                session.member.user_obj["iUpgDays"] = days
            await send_obj(writer, {"Cmd": "upgradeSync", "Coins": session.char["coins"],
                                    "UpgradeDays": days, "UpgradeExpires": expires})
        await send_obj(writer, {"Cmd": "chatm", "Name": "Server", "channel": "server",
                                "ID": 0,
                                "msg": f"{target['name']} membership set to {days} day(s)."})
        print(f"  [member] {target['name']} -> {days} day(s), expires {expires}")
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
            # A first house deed auto-equips, same as the buy path.
            game.give_item(session.conn, session.char, item_id, qty)
            eq = game.auto_equip_first_house(session.conn, session.char, item_id)
            if eq is not None:
                await send_obj(writer, eq)
            note = " Equipped as your home!" if eq is not None else ""
            await send_obj(writer, {"Cmd": "chatm", "Name": "Server", "channel": "server",
                                    "ID": 0,
                                    "msg": f"+{qty} {idef.get('Name') or item_id} "
                                           f"(house item).{note} Relog to see it in the "
                                           f"house menu."})
            print(f"  [item/house] {session.char['name']} +{qty} of {item_id}"
                  f"{' (auto-equipped)' if eq is not None else ''}")
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
    log_unhandled("cmd", msg)
    return
