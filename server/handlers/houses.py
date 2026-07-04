"""Houses: enter a house (yours or any player's) + persist a house layout.
(First-house auto-equip on buy lives in economy.buy_sell; the deed equip route
lives in items.equip — the client sends plain buyItem/equipItem for those.)"""
import game
import maps

from .registry import register
from .context import send_obj, _enter_area


@register("housesave")
async def house_save(session, writer, cmd, params, msg):
    # persist a house layout (the save action works)
    if session.char is None:
        return
    await send_obj(writer, game.house_save(
        session.conn, session.char, params[0] if params else "0",
        params[1] if len(params) > 1 else "", params[2] if len(params) > 2 else "[]"))
    print(f"  [s2c] houseSave (map {params[0] if params else '?'})")
    return


@register("house")
async def house(session, writer, cmd, params, msg):
    # Enter a house (RequestHouse: no params = your own; Params=[name] = visit ANY
    # player's — the owner does NOT need to be online, their house lives in the DB).
    # A house is a normal AreaJoin carrying area.houseData (mapHouseData: saved
    # placements + the owner's furniture list + owner name) — the client builds the
    # map like any area and HouseItemManager places the furniture. Instanced per owner
    # as <houseMap>-<ownerUID>, matching AE's captured "house-508915". [[houses-doable]]
    if session.char is None or session.member is None:
        return
    owner_char = session.char
    if params and str(params[0]).strip():           # /house <name> -> visit
        owner_char = session.conn.execute(
            "SELECT * FROM characters WHERE LOWER(name)=LOWER(?) ORDER BY id LIMIT 1",
            (str(params[0]).strip(),)).fetchone()
        if owner_char is None:
            await send_obj(writer, {"Cmd": "chatm",
                                    "msg": f'There is no player named "{params[0]}".',
                                    "Name": "Server", "channel": "server", "ID": 0})
            return
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
