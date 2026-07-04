"""Chat / emote / whisper / modyell / summon / goto — player-to-player commands."""
import json

import game
import world

from .registry import register
from .context import send_obj, _enter_area


# --- teleport: /goto (go to a player) + /summon (invite a player to you) ---
@register("GoToPlayer")
async def goto_player(session, writer, cmd, params, msg):   # Params=[playerName] -> join their instance
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


@register("si")
async def summon_invite(session, writer, cmd, params, msg):  # summon invite: Params=[targetName]
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


@register("sa", "sd")
async def summon_answer(session, writer, cmd, params, msg):  # summon accept / decline: Params=[summonerID]
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


@register("message", "chat")
async def chat(session, writer, cmd, params, msg):  # RequestChat -> Params = [msg, channel, target?]
    if session.member is not None and params:
        msg_text = params[0]
        channel = params[1] if len(params) > 1 else "zone"
        pk = {"Cmd": "chatm", "msg": msg_text, "Name": session.member.name,
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
        print(f"  [chatm/{channel}] {session.member.name}: {msg_text}")
    return


@register("emotea")
async def emote(session, writer, cmd, params, msg):
    if session.member is None or session.char is None:
        return
    # echo emote to the WHOLE area, incl. the sender: the typed "/emote" path
    # (HandleEmote) has no local playback and needs the echo.
    pk = {"Cmd": "emotea", "userID": session.member.uid,
          "strEmote": params[0] if params else ""}
    world.broadcast(session.area, pk)
    print(f"  [emotea] {pk['strEmote']}")
    return


async def modyell(session, writer, params):
    """/modyell (and /yell): server-wide gold announcement to EVERY connected player.
    Staff-only (access >= 40); /modyell falls through RequestCmd so the client doesn't
    gate it — we do. Reached via the "cmd" envelope (see dev.slash_cmd)."""
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
