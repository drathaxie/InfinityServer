"""Gems / enhancements (patterns): empower an item so it can be equipped.

Applying/removing a gem RECOMPUTES the player's stats (keystone): the equipped gems are
the source of the primary stats + the weapon's damage range. _refresh_pattern_stats pushes
the new attack power/weapon range into the combat engine and returns the recomputed `sta`
the UpdatePattern carries back (1=1: the capture's UpdatePattern.stats IS the stat refresh,
no separate statUpdate follows)."""
import patterns
import loot

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
        consumed = resp.pop("_consumedPatternID", None)
        bounced = resp.pop("_bouncedPatternItem", None)
        if resp.get("Success"):
            resp["stats"] = _refresh_pattern_stats(session)
        await send_obj(writer, resp)
        if resp.get("Success") and consumed is not None:
            await send_obj(writer, {"Cmd": "removePattern", "CharPatternID": consumed})
        if resp.get("Success") and bounced is not None:
            await send_obj(writer, {"Cmd": "addItems", "items": [],
                                    "patternItems": [bounced], "bankedItems": []})
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


@register("dustPattern")
async def dust_pattern(session, writer, cmd, params, msg):
    # RequestDustPattern(CharPatternID, LootID): dust an owned bag gem when LootID=-1, or a gem
    # directly from the pending loot window when LootID identifies that drop.
    if session.char is None or not params:
        return
    char_pattern_id = int(params[0])
    loot_id = int(params[1]) if len(params) > 1 else -1
    loot_item_id = None
    if loot_id >= 0 and session.member is not None:
        pending = loot.dust_pending_pattern(session.member.uid, loot_id)
        if pending is None:
            resp = patterns._dust_response(
                session.conn, session.char["id"], char_pattern_id, success=False,
                message="That gem is no longer available.")
        else:
            pat, loot_item_id = pending
            resp = patterns.dust_loot_pattern(
                session.conn, session.char["id"], char_pattern_id, pat)
    else:
        resp = patterns.dust_pattern(session.conn, session.char["id"], char_pattern_id)
    await send_obj(writer, resp)
    # ResponseDust updates the currency counter. The stock client relies on the normal
    # removePattern response to evict the consumed gem and close PatternPreview.
    if resp["bSuccess"]:
        await send_obj(writer, {
            "Cmd": "removePattern", "CharPatternID": char_pattern_id,
        })
        # ResponseGetLoot is the client's only pending-loot eviction path. ResponseDust has no
        # LootID field, so a direct-from-drop dust also needs this acknowledgement.
        if loot_item_id is not None:
            await send_obj(writer, {
                "Cmd": "getLoot", "bSuccess": True, "message": "",
                "ItemID": loot_item_id, "LootID": loot_id,
            })
    print(f"  [s2c] dustPattern pattern={char_pattern_id} loot={loot_id} "
          f"-> {'+' + str(resp['Gained']) if resp['bSuccess'] else 'miss'} dust")
    return


async def _send_trait_result(session, writer, resp, what):
    """Deliver a trait change. A gear gem rides UpdatePattern (which is how the client applies a
    pattern to an inventory item, and which also carries the stat refresh when the gear is worn).
    A loose bag gem has no UpdatePattern path at all -- Inventory.UpdatePattern resolves ItemID
    against gear and bank only -- so its refresh goes out as addItems.patternItems, the same
    channel the equipPattern bounce already uses."""
    target = resp.pop("_target", None)
    key = resp.pop("_key", None)
    if not resp.get("Success"):
        await send_obj(writer, resp)
        print("  [s2c] %s -> %s" % (what, resp.get("errorMessage")))
        return
    if target == "gear":
        resp["stats"] = _refresh_pattern_stats(session)
        await send_obj(writer, resp)
    else:
        await send_obj(writer, {"Cmd": "addItems", "items": [], "bankedItems": [],
                                "patternItems": [{"CharPatternID": int(key),
                                                  "pattern": resp["pattern"], "LootID": -1}]})
    pat = resp.get("pattern") or {}
    print("  [s2c] %s -> %s slots %s/%s" % (what, target, pat.get("SlotsUsed"),
                                            pat.get("MaxSlots")))


@register("applyTrait")
async def apply_trait(session, writer, cmd, params, msg):
    # RequestApplyTrait Params=[ShopID, ShopItemID, CharItemID, CharPatternID]. PatternPreview
    # sends the gear as a CATALOG id and -1 for the gem, or the gem as a CharPatternID and -1
    # for the gear. It has already checked the duplicate/slot rules client-side; the server
    # re-checks them because that check is the only thing standing between a crafted packet and
    # an over-stuffed gem.
    if session.char is None or len(params) < 4:
        return
    resp = patterns.apply_trait(session.conn, session.char["id"],
                                params[0], params[1], params[2], params[3])
    await _send_trait_result(session, writer, resp, "applyTrait")
    return


@register("removeTrait")
async def remove_trait(session, writer, cmd, params, msg):
    # RequestRemoveTrait Params=[TraitItemID, CharItemID, CharPatternID] -> frees the slots that
    # trait was spending. The trait itself is consumed, which is what the client confirmation
    # promises ("It frees N slots", not "returns the trait").
    if session.char is None or len(params) < 3:
        return
    resp = patterns.remove_trait(session.conn, session.char["id"],
                                 params[0], params[1], params[2])
    await _send_trait_result(session, writer, resp, "removeTrait")
    return
