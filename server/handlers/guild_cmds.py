"""Guild commands: create (gc), invite (gi) + accept/decline (gia/gid), promote/demote
(gp/gd), kick (gk), MOTD (gmotd). Persistent via the guilds table + characters.guild_id.
See guilds.py for the wire shapes."""
import game
import guilds
import maps
import world

from .registry import register
from .context import send_obj, _enter_area


def _reload_char(session):
    session.conn.commit()
    session.char = session.conn.execute(
        "SELECT * FROM characters WHERE id=?", (session.char["id"],)).fetchone()


async def _server_msg(writer, text):
    await send_obj(writer, {"Cmd": "chatm", "msg": text, "Name": "Server",
                            "channel": "server", "ID": 0})


@register("gc")
async def guild_create(session, writer, cmd, params, msg):     # Params=[name]
    if session.char is None or session.member is None or not params:
        return
    name = str(params[0]).strip()
    if not name:
        return
    gid = guilds.create(session.conn, session.char, name)
    if gid is None:
        await _server_msg(writer, "Could not create guild (name taken or you're already in one).")
        return
    _reload_char(session)
    session.member.user_obj["guild"] = guilds.guild_object(session.conn, gid)
    await send_obj(writer, {"Cmd": "newGuild", "guild": guilds.guild_object(session.conn, gid)})
    print(f"  [guild] {session.member.name} created '{name}' (#{gid})")
    return


@register("gi")
async def guild_invite(session, writer, cmd, params, msg):     # Params=[playerName]
    if session.char is None or session.member is None or not params:
        return
    gid = session.char["guild_id"]
    if not gid or session.char["guild_rank"] < guilds.RANK_OFFICER:
        await _server_msg(writer, "Only guild officers can invite.")
        return
    target = world.find_member(params[0])
    if target is None:
        await _server_msg(writer, f'"{params[0]}" is not online.')
        return
    g = session.conn.execute("SELECT name FROM guilds WHERE id=?", (gid,)).fetchone()
    world.send(target, {"Cmd": "guildinvite", "guildID": gid,
                        "owner": session.member.name, "gName": g["name"]})
    print(f"  [guild] {session.member.name} invited {target.name} to #{gid}")
    return


@register("gia")
async def guild_invite_accept(session, writer, cmd, params, msg):   # Params=[ownerName]
    if session.char is None or session.member is None or not params:
        return
    if session.char["guild_id"]:                # already in a guild
        return
    owner = session.conn.execute("SELECT guild_id FROM characters WHERE lower(name)=?",
                                 (str(params[0]).strip().lower(),)).fetchone()
    if owner is None or not owner["guild_id"]:
        return
    gid = owner["guild_id"]
    if len(guilds.members(session.conn, gid)) >= guilds.MAX_MEMBERS:
        await _server_msg(writer, "That guild is full.")
        return
    session.conn.execute("UPDATE characters SET guild_id=?, guild_rank=? WHERE id=?",
                         (gid, guilds.RANK_MEMBER, session.char["id"]))
    _reload_char(session)
    session.member.user_obj["guild"] = guilds.guild_object(session.conn, gid)
    # tell existing members about the newcomer, then hand the joiner the full guild
    me = session.conn.execute("SELECT id, name, level, guild_rank FROM characters WHERE id=?",
                              (session.char["id"],)).fetchone()
    guilds.broadcast(session.conn, gid, {"Cmd": "gAddMem", "player": guilds._guild_player(me)},
                     exclude_char_id=session.char["id"])
    guilds.broadcast(session.conn, gid, {"Cmd": "joinGuild", "UserID": session.char["id"],
                                         "guildName": session.member.user_obj["guild"]["Name"]})
    await send_obj(writer, {"Cmd": "newGuild", "guild": guilds.guild_object(session.conn, gid)})
    print(f"  [guild] {session.member.name} joined #{gid}")
    return


@register("gid")
async def guild_invite_decline(session, writer, cmd, params, msg):   # no state change
    return


async def _rank_change(session, writer, params, new_rank, out_cmd):
    if session.char is None or not params:
        return
    if not session.char["guild_id"] or session.char["guild_rank"] < guilds.RANK_LEADER:
        await _server_msg(writer, "Only the guild leader can do that.")
        return
    try:
        target_id = int(params[0])
    except (ValueError, TypeError):
        return
    trow = session.conn.execute("SELECT guild_id FROM characters WHERE id=?", (target_id,)).fetchone()
    if trow is None or trow["guild_id"] != session.char["guild_id"]:
        return
    session.conn.execute("UPDATE characters SET guild_rank=? WHERE id=?", (new_rank, target_id))
    session.conn.commit()
    guilds.broadcast(session.conn, session.char["guild_id"],
                     {"Cmd": out_cmd, "rank": new_rank, "userID": target_id})
    return


@register("gp")
async def guild_promote(session, writer, cmd, params, msg):    # Params=[userID]
    await _rank_change(session, writer, params, guilds.RANK_OFFICER, "guildPromote")


@register("gd")
async def guild_demote(session, writer, cmd, params, msg):     # Params=[userID]
    await _rank_change(session, writer, params, guilds.RANK_MEMBER, "guildDemote")


@register("gk")
async def guild_kick(session, writer, cmd, params, msg):       # Params=[userID]
    if session.char is None or not params:
        return
    if not session.char["guild_id"] or session.char["guild_rank"] < guilds.RANK_LEADER:
        await _server_msg(writer, "Only the guild leader can kick.")
        return
    try:
        target_id = int(params[0])
    except (ValueError, TypeError):
        return
    if target_id == session.char["id"]:
        return
    gid = session.char["guild_id"]
    trow = session.conn.execute("SELECT guild_id, name FROM characters WHERE id=?",
                                (target_id,)).fetchone()
    if trow is None or trow["guild_id"] != gid:
        return
    session.conn.execute("UPDATE characters SET guild_id=0, guild_rank=0 WHERE id=?", (target_id,))
    session.conn.commit()
    guilds.broadcast(session.conn, gid, {"Cmd": "guildRemove", "UserID": target_id})
    kicked = world.find_member(trow["name"])     # tell the kicked player too
    if kicked is not None:
        kicked.user_obj["guild"] = None
        world.send(kicked, {"Cmd": "guildRemove", "UserID": target_id})
    print(f"  [guild] {session.char['name']} kicked {target_id} from #{gid}")
    return


async def guild_leave(session, writer, params=None):
    """/gleave (via the `cmd` envelope — no native client command). Removes you from your guild;
    promotes the top remaining member if you were the leader; disbands an empty guild. The client
    can't self-clear its own guild panel live (ResponseGuildRemove ignores UserID==self), so we
    null the cached guild and tell them to relog to refresh — same limit as a kick."""
    if session.char is None or session.member is None:
        return
    if not session.char["guild_id"]:
        await _server_msg(writer, "You're not in a guild.")
        return
    result = guilds.leave(session.conn, session.char)
    _reload_char(session)
    if result is None:
        return
    gid = result["gid"]
    # tell the remaining members you're gone (UserID != their own id, so their roster updates)
    guilds.broadcast(session.conn, gid, {"Cmd": "guildRemove", "UserID": result["left_id"]})
    if result["new_leader_id"]:
        guilds.broadcast(session.conn, gid, {"Cmd": "guildPromote",
                                             "rank": guilds.RANK_LEADER,
                                             "userID": result["new_leader_id"]})
    session.member.user_obj["guild"] = None
    note = " The guild was disbanded (nobody left in it)." if result["disbanded"] else ""
    await _server_msg(writer, f"You left the guild.{note} Relog to refresh your guild panel.")
    print(f"  [guild] {session.member.name} left #{gid}"
          f"{' (disbanded)' if result['disbanded'] else ''}")
    return


async def guild_hall(session, writer, params=None):
    """/guildhall [guildName] (via the `cmd` envelope). No arg -> your OWN guild's hall; an arg
    visits another guild's hall (owner offline is fine — the hall lives in the DB). The hall is a
    house-style AreaJoin carrying houseData built from the guild's saved layout + the LEADER's
    furniture; only the leader (unm match) gets decorate controls. Instanced per guild
    (<map>-g<gid>) so all members share one room."""
    if session.char is None or session.member is None:
        return
    params = params or []
    # a guild name can have spaces (it arrives split across params), so re-join — same as /gc.
    name = " ".join(str(p) for p in params).strip()
    if name:                                        # visit a named guild's hall
        g = guilds.guild_by_name(session.conn, name)
        if g is None:
            await _server_msg(writer, f'There is no guild named "{name}".')
            return
        gid = g["id"]
    else:                                           # your own guild's hall
        gid = session.char["guild_id"]
        if not gid:
            await _server_msg(writer, "You're not in a guild. Create one with /gc <name>.")
            return
    hd = game.build_guild_hall_data(session.conn, gid)
    if hd is None:
        await _server_msg(writer, "That guild has no leader to host a hall.")
        return
    map_name = guilds.hall_map(session.conn, gid)
    if maps.area_payload(map_name, session.conn) is None:
        await _server_msg(writer, f"The guild hall map ('{map_name}') isn't available yet.")
        return
    await send_obj(writer, game.change_state(session.char))
    await _enter_area(session, writer, map_name, f"g{gid}", house_data=hd)
    session.guildhall_gid = gid                     # route the leader's housesave to guild storage
    gname = session.conn.execute("SELECT name FROM guilds WHERE id=?", (gid,)).fetchone()["name"]
    print(f"  [guildhall] {session.char['name']} -> '{gname}' hall ({map_name}-g{gid}, "
          f"{len(hd['items'])} items)")
    return


@register("gmotd")
async def guild_motd(session, writer, cmd, params, msg):       # Params=[msg]
    if session.char is None or not params:
        return
    if not session.char["guild_id"] or session.char["guild_rank"] < guilds.RANK_OFFICER:
        await _server_msg(writer, "Only guild officers can set the MOTD.")
        return
    motd = str(params[0])
    session.conn.execute("UPDATE guilds SET motd=? WHERE id=?", (motd, session.char["guild_id"]))
    session.conn.commit()
    guilds.broadcast(session.conn, session.char["guild_id"], {"Cmd": "gMOTD", "MOTD": motd})
    return
