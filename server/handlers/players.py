"""Player-info commands surfaced by the playtest client: inspecting another player's
gear, querying an item's full detail, gender swap, statue generation, and the
membership (upgrade) resync. All self-contained request/response pairs."""
import time

import db
import game
import world
import friends as friendsvc

from .registry import register
from .context import send_obj


@register("inspectPlayer")
async def inspect_player(session, writer, cmd, params, msg):
    # Params=[targetUID]. Reply {Cmd:inspectPlayer, items:[...]} with the target's EQUIPPED
    # gear (client builds an Inventory(items) and opens the hero-stat panel). RequestInspectPlayer
    if session.member is None or not params:
        return
    try:
        target = world.find_uid(int(params[0]))
    except (ValueError, TypeError):
        return
    if target is None:
        return
    row = session.conn.execute("SELECT id FROM characters WHERE lower(name)=?",
                               (target.name.lower(),)).fetchone()
    if row is None:
        return
    items = [it for it in game.inventory(session.conn, row["id"]) if it.get("Equipped")]
    await send_obj(writer, {"Cmd": "inspectPlayer", "items": items})
    return


@register("itemQuery")
async def item_query(session, writer, cmd, params, msg):
    # Params=[itemID]. Reply {Cmd:itemQuery, item:{...}} with the full catalog item. RequestItemQuery
    if not params:
        return
    try:
        item = db.item(session.conn, int(params[0]))
    except (ValueError, TypeError):
        item = None
    if item is None:
        return
    await send_obj(writer, {"Cmd": "itemQuery", "item": item})
    return


@register("genderSwap")
async def gender_swap(session, writer, cmd, params, msg):
    # Flip M<->F, re-resolve the hair for the new gender (hair bundles are gendered), persist,
    # update the render object, and broadcast so every client re-creates this avatar. RequestGenderSwap
    if session.char is None or session.member is None:
        return
    new_gender = "F" if str(session.char["gender"]).upper() == "M" else "M"
    hair_id = session.char["hair_id"]
    hi = game._hair_info(session.conn, hair_id, new_gender)
    hair_name = hi.get("Name") if hi else None
    session.conn.execute("UPDATE characters SET gender=? WHERE id=?",
                         (new_gender, session.char["id"]))
    session.conn.commit()
    session.char = session.conn.execute(
        "SELECT * FROM characters WHERE id=?", (session.char["id"],)).fetchone()
    # keep the render object current for late-joiners
    session.member.user_obj["strGender"] = new_gender
    if hi is not None:
        cust = session.member.user_obj.setdefault("customization", {})
        cust["HairName"] = hair_name
        cust["HairBundle"] = hi.get("Bundle")
    pk = {"Cmd": "genderSwap", "success": True, "uid": session.member.uid,
          "gender": new_gender, "HairID": hair_id, "strHairName": hair_name or "",
          "coins": session.char["coins"]}
    world.broadcast(session.area, pk)
    print(f"  [s2c] genderSwap uid={session.member.uid} -> {new_gender}")
    return


@register("generateStatue")
async def generate_statue(session, writer, cmd, params, msg):
    # Hall-of-heroes statue generation. We don't run the AE statue pipeline, so answer with a
    # clean, non-cooldown "unavailable" result rather than leaving the button spinning. RequestGenerateStatue
    if session.member is None:
        return
    await send_obj(writer, {"Cmd": "generateStatue", "Success": False, "ItemID": 0,
                            "Message": "Statue generation isn't available on this server yet.",
                            "CooldownRemainingMs": 0})
    return


# --- friends: request / accept / decline / delete ---
@register("requestFriend")
async def friend_request(session, writer, cmd, params, msg):   # Params=[targetName]
    if session.char is None or session.member is None or not params:
        return
    target = world.find_member(params[0])
    if target is None:
        await send_obj(writer, {"Cmd": "chatm", "msg": f'"{params[0]}" is not online.',
                                "Name": "Server", "channel": "server", "ID": 0})
        return
    if target.uid != session.member.uid:
        world.send(target, {"Cmd": "requestFriend", "unm": session.member.name})
        print(f"  [friend] {session.member.name} requested {target.name}")
    return


@register("addFriend")
async def friend_accept(session, writer, cmd, params, msg):     # Params=[requesterName]
    # The accepter's client sends addFriend with the requester's name. Link both ways and push
    # each side an addFriend {friend} with the OTHER's FriendObject so both lists update live.
    if session.char is None or session.member is None or not params:
        return
    other = friendsvc._char_by_name(session.conn, params[0])
    if other is None or other["id"] == session.char["id"]:
        return
    friendsvc.link(session.conn, session.char["id"], other["id"])
    session.conn.commit()
    await send_obj(writer, {"Cmd": "addFriend",
                            "friend": friendsvc.friend_object(session.conn, other["id"])})
    om = world.find_member(other["name"])          # push to the requester if they're online
    if om is not None:
        world.send(om, {"Cmd": "addFriend",
                        "friend": friendsvc.friend_object(session.conn, session.char["id"])})
    print(f"  [friend] {session.member.name} <-> {other['name']} linked")
    return


@register("declineFriend")
async def friend_decline(session, writer, cmd, params, msg):    # Params=[requesterName] — no state
    return


@register("deleteFriend")
async def friend_delete(session, writer, cmd, params, msg):     # Params=[friendCharID]
    if session.char is None or session.member is None or not params:
        return
    try:
        fid = int(params[0])
    except (ValueError, TypeError):
        return
    friendsvc.unlink(session.conn, session.char["id"], fid)
    session.conn.commit()
    await send_obj(writer, {"Cmd": "deleteFriend", "ID": fid})
    # tell the ex-friend (if online) to drop us too
    row = session.conn.execute("SELECT name FROM characters WHERE id=?", (fid,)).fetchone()
    if row is not None:
        om = world.find_member(row["name"])
        if om is not None:
            world.send(om, {"Cmd": "deleteFriend", "ID": session.char["id"]})
    print(f"  [friend] {session.member.name} removed friend {fid}")
    return


@register("upgradeSync")
async def upgrade_sync(session, writer, cmd, params, msg):
    # Client asks to resync membership/coins (e.g. after a store return). Echo the character's
    # current values — we're a free server, so UpgradeDays stays 0. RequestUpgradeSync
    if session.char is None:
        return
    await send_obj(writer, {"Cmd": "upgradeSync", "Coins": session.char["coins"],
                            "UpgradeDays": 0,
                            "UpgradeExpires": game.DEFAULT_UPGRADE_EXPIRES})
    return
