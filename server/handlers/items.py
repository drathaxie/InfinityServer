"""Inventory item actions: equip (armor/class/house-deed routing), unequip,
removeItem, and /charedit's changeColor."""
import combat
import forge
import game
import world

from .registry import register
from .context import send_obj, _refresh_pattern_stats, UNHANDLED


@register("removeItem")
async def remove_item(session, writer, cmd, params, msg):
    # Params=[itemID, qty] — delete from inventory
    if session.char is not None:
        session.char = session.conn.execute(
            "SELECT * FROM characters WHERE id=?", (session.char["id"],)).fetchone()
        cid = game.remove_item(session.conn, session.char, params)
        print(f"  [removeItem] {params} -> char_item {cid}")
    return


@register("equipItem")
async def equip_item(session, writer, cmd, params, msg):
    # equipping a CLASS armor switches skills
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
                # data-driven classes swap ONTO the engine here — and back OFF it when the
                # player equips a class that still runs on the Python path
                combat.set_class_rules(uid, forge.rules_for_class(session.conn, new_class))
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
    return UNHANDLED


@register("unequipItem")
async def unequip_item(session, writer, cmd, params, msg):
    # RequestUnequipItem -> Params=[itemID]
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


# --- /charedit : persist appearance (colours + hair), recolour avatars live ----
@register("savePortrait")
async def save_portrait(session, writer, cmd, params, msg):
    # Name-plate style picker. Params=[pref]. Persist it, then broadcast portraitChange to the
    # WHOLE area (incl. self) so every client swaps this player's overhead name-plate live.
    if session.char is None or session.member is None:
        return
    try:
        want = int(params[0]) if params else 0
    except (TypeError, ValueError):
        want = 0
    # Server-authoritative: the client only shows owned frames, but never trust it — a locked
    # frame falls back to Default(0) rather than being persisted/broadcast. [[name-plates]]
    ach = {}
    try:
        import json as _json
        ach = _json.loads(session.char["achievements"] or "{}")
    except (ValueError, TypeError):
        ach = {}
    uname = game.account_username(session.conn, session.char)
    if want not in game.owned_portrait_frames(uname, ach):
        want = 0
    pref = game.save_portrait_pref(session.conn, session.char, want)
    session.conn.commit()
    session.char = session.conn.execute(
        "SELECT * FROM characters WHERE id=?", (session.char["id"],)).fetchone()
    # keep the render object current so late-joiners (AreaAdd/uoBranch) see the new plate.
    session.member.user_obj["portraitPref"] = pref
    world.broadcast(session.area,
                    {"Cmd": "portraitChange", "uid": session.member.uid, "portraitPref": pref})
    print(f"  [s2c] portraitChange uid={session.member.uid} pref={pref}")
    return


@register("savePrefs")
async def save_prefs(session, writer, cmd, params, msg):
    # userPrefs UI toggle. Params=[name, "True"/"False"]. Persist so it survives relog; keep the
    # render object's showHelm/showCloak current so late-joiners (AreaAdd/uoBranch) see it.
    if session.char is None or session.member is None or len(params) < 2:
        return
    val = game.save_user_pref(session.conn, session.char, params[0], params[1])
    if val is None:                       # unknown pref key — ignore
        return
    session.conn.commit()
    session.char = session.conn.execute(
        "SELECT * FROM characters WHERE id=?", (session.char["id"],)).fetchone()
    if params[0] == "ShowHelm":
        session.member.user_obj["showHelm"] = val
    elif params[0] == "ShowCloak":
        session.member.user_obj["showCloak"] = val
    return


@register("changeColor")
async def change_color(session, writer, cmd, params, msg):
    # Params=[Skin,Eye,Hair,Base,Trim,Accessory,HairID]
    if session.char is None or session.member is None:
        return
    applied = game.save_customization(session.conn, session.char, params)
    session.conn.commit()
    session.char = session.conn.execute(
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
              "hairID": char["hair_id"]}
        # include the chosen hair's bundle so the client swaps the hairSTYLE live (matches AE,
        # which sends HairBundle in changeColor) — not just the hair colour.
        _hi = game._hair_info(session.conn, char["hair_id"], char["gender"])
        pk["hairBundle"] = _hi.get("Bundle")
        world.broadcast(session.area, pk)
        # keep the render object current so late-joiners (AreaAdd/uoBranch) see the new look.
        cust = session.member.user_obj.setdefault("customization", {})
        for jk, col in game._COLOR_COLS.items():
            cust[jk] = char[col]
        cust["HairID"] = char["hair_id"]
        print(f"  [s2c] changeColor (charedit) uid={session.member.uid} {applied}")
    return
