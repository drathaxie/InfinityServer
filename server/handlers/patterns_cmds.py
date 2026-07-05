"""Gems / enhancements (patterns): empower an item so it can be equipped.

Applying/removing a gem RECOMPUTES the player's stats (keystone): the equipped gems are
the source of the primary stats + the weapon's damage range. _refresh_pattern_stats pushes
the new attack power/weapon range into the combat engine and returns the recomputed `sta`
the UpdatePattern carries back (1=1: the capture's UpdatePattern.stats IS the stat refresh,
no separate statUpdate follows)."""
import patterns

from .registry import register
from .context import send_obj, _refresh_pattern_stats


@register("itemdefaultpattern")
async def item_default_pattern(session, writer, cmd, params, msg):
    # "Power Up": mint+apply a default gem
    if session.char is not None and params:
        resp = patterns.item_default_pattern(session.conn, session.char["id"], params[0])
        if resp.get("Success"):
            resp["stats"] = _refresh_pattern_stats(session)
        await send_obj(writer, resp)
        print(f"  [s2c] UpdatePattern (item {params[0]} powered up, "
              f"Success={resp.get('Success')})")
    return


@register("equipPattern")
async def equip_pattern(session, writer, cmd, params, msg):
    # apply a chosen gem to an owned item
    if session.char is not None and len(params) >= 2:
        resp = patterns.equip_pattern(
            session.conn, session.char["id"], int(params[0]), int(params[1]),
            int(params[2]) if len(params) > 2 else -1)
        if resp.get("Success"):
            resp["stats"] = _refresh_pattern_stats(session)
        await send_obj(writer, resp)
        print(f"  [s2c] UpdatePattern (equipPattern {params})")
    return


@register("removePattern")
async def remove_pattern(session, writer, cmd, params, msg):
    # clear a gem from an item
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
