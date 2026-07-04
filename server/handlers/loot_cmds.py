"""Loot / drops: keep pending drops out of the Loot Inventory window (capture)."""
import loot

from .registry import register
from .context import send_obj


@register("getDrop")
async def get_drop(session, writer, cmd, params, msg):
    # keep ONE drop -> addItems + getLoot
    if session.char is not None and session.member is not None and len(params) >= 2:
        add, got = loot.take(session.conn, session.char["id"], session.member.uid,
                             params[0], params[1])
        if add is not None:
            await send_obj(writer, add)
        await send_obj(writer, got)
        print(f"  [s2c] getDrop item={params[0]} loot={params[1]} -> "
              f"{'kept' if add else 'miss'}")
    return


@register("bulkOperation")
async def bulk_operation(session, writer, cmd, params, msg):
    # keep ALL (IsLootAll) or discard all
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
