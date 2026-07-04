"""Shops + bank: loadShop/loadHairShop, buy/sell, loadBank + bank moves."""
import game

from .registry import register
from .context import send_obj, load_shop


@register("buyItem", "sellItem")
async def buy_sell(session, writer, cmd, params, msg):
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
    # First house auto-equips: buying a deed with no home yet immediately makes it home
    # (equipHouse sets EquippedHouseItemID + flips bEquip on the just-bought list entry).
    if cmd == "buyItem" and resp.get("Success") and resp.get("houseItem"):
        session.char = session.conn.execute(
            "SELECT * FROM characters WHERE id=?", (session.char["id"],)).fetchone()
        eq = game.auto_equip_first_house(session.conn, session.char,
                                         resp["houseItem"]["ItemID"])
        if eq is not None:
            await send_obj(writer, eq)
            print(f"  [s2c] equipHouse (first house auto-equip: "
                  f"{resp['houseItem']['ItemID']})")
    print(f"  [s2c] {cmd} (Success={resp.get('Success')})")
    return


@register("loadShop")
async def load_shop_cmd(session, writer, cmd, params, msg):
    await send_obj(writer, load_shop(session.conn, params))
    print("  [s2c] loadShop")
    return


@register("loadHairShop")
async def load_hairshop(session, writer, cmd, params, msg):
    # HairShop apop button -> hair catalog (PUBLIC path to
    # character customization; opens the customize overlay)
    try:
        shop_id = int(params[0]) if params else 0
    except (ValueError, TypeError):
        shop_id = 0
    resp = game.load_hairshop(session.conn, shop_id)
    await send_obj(writer, resp)
    print(f"  [s2c] loadHairShop ({shop_id}, {len(resp['hair'])} hairs)")
    return


@register("loadBank")
async def load_bank(session, writer, cmd, params, msg):
    # RequestLoadBank (no params) -> the char's banked items.
    # ResponseLoadBank.Cmd is "LoadBank" (capital B); items
    # feed playerInventory.setupBank.
    if session.char is None:
        return
    items = game.bank(session.conn, session.char["id"])
    await send_obj(writer, {"Cmd": "LoadBank", "items": items})
    print(f"  [s2c] LoadBank ({len(items)} items)")
    return


@register("bankFromInv", "bankToInv", "bankSwapInv")
async def bank_move(session, writer, cmd, params, msg):
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
