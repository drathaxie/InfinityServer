"""
Loot / drops — the kill-reward + Loot Inventory flow (live capture 2026-06-18).

Wire (verbatim from capture, lines 57964 / 61398-61401 / 63313-63315):
  s2c rewardPlayer  {CP:{val},Exp:{val},ExpTotal,typ:"m",monID,factionID,Gold:{val},
                     items:[{LootID,Quantity,ID,<catalog fields>}],autoDiscarded:[],
                     showDropWindow:true,showGold:true}   -- on kill; items[] = PENDING loot
  c2s getDrop       {ItemID,LootID,Params:[ItemID,LootID]}            -- keep ONE pending drop
  s2c addItems      {items:[{LootID:-1,CharItemID,Quantity,ID,...}]}  -- it lands in inventory
  s2c getLoot       {bSuccess:true,ItemID,LootID}                     -- remove it from the window
  c2s bulkOperation {IsLootAll:true,Params:["1"]}                     -- keep ALL
  s2c addItems{...all...} + bulkOperation{Success,IsLootAll,consumedLoot:[...]}

Pending loot lives server-side per uid until the player keeps it (getDrop / bulkOperation),
discards it, or leaves. Drops roll from a flagged material loot table — no per-monster drop
table was captured, so the table + per-kill chance are OUR design (the MECHANIC is 1=1).
"""
import json
import random

import game

# LootIDs the client keys drops by — monotonic and above the captured live range so a real
# pending drop can never collide with a replayed sample's LootID.
_next_loot = 2_700_000
_pending = {}               # uid -> [ {loot_id, item_id, quantity, raw} ]

# Flagged loot table: real catalog MATERIAL items (ItemType 4) a kill can drop. The specific
# per-monster drop table isn't in the capture, so this shared table + chance is a design choice.
DROP_TABLE = [4770, 4771, 5357, 87479, 6136]    # Dark Crystal Shard, Diamond/Totem of Nulgath,
                                                # Red Dragon Scales, Gem of Nulgath
DROP_CHANCE = 0.5           # chance a kill drops anything at all
MAX_DROPS = 2               # up to this many separate drops per kill


def _lid():
    global _next_loot
    _next_loot += 1
    return _next_loot


def _catalog(conn, item_id):
    row = conn.execute("SELECT raw FROM items WHERE item_id=?", (int(item_id),)).fetchone()
    return json.loads(row["raw"]) if row else None


def roll_drops(conn, mon_id=None):
    """Roll this kill's drops -> a list of catalog item dicts (possibly []).

    If the monster has an authored table in `monster_drops`, each row rolls INDEPENDENTLY
    (random() < rate -> that item drops, at its quantity), matching AQW's per-item-rate model.
    A monster with NO authored rows falls back to the shared global pool (DROP_TABLE/DROP_CHANCE),
    so un-authored monsters still drop something."""
    rows = []
    try:
        mid = int(mon_id) if mon_id is not None else None
    except (TypeError, ValueError):
        mid = None
    if mid is not None:
        rows = conn.execute(
            "SELECT item_id, rate, quantity FROM monster_drops WHERE mon_id=?", (mid,)).fetchall()
    if rows:                                            # per-monster table: independent per-item rate
        out = []
        for r in rows:
            if random.random() < float(r["rate"] or 0):
                item = _catalog(conn, r["item_id"])
                if item:
                    item = dict(item)
                    item["Quantity"] = int(r["quantity"] or 1)
                    out.append(item)
        return out
    # fallback: shared global pool for monsters without an authored drop table
    if random.random() > DROP_CHANCE:
        return []
    out = []
    for _ in range(random.randint(1, MAX_DROPS)):
        item = _catalog(conn, random.choice(DROP_TABLE))
        if item:
            out.append(item)
    return out


def add_pending(uid, items):
    """Assign a LootID to each rolled drop, store it as PENDING for this player, and return the
    rewardPlayer.items wire list (catalog fields + LootID + Quantity)."""
    bag = _pending.setdefault(uid, [])
    wire = []
    for item in items:
        lid = _lid()
        qty = int(item.get("Quantity", 1) or 1)
        bag.append({"loot_id": lid, "item_id": int(item.get("ID", 0) or 0),
                    "quantity": qty, "raw": item})
        w = dict(item)
        w["LootID"] = lid
        w["Quantity"] = qty
        wire.append(w)
    return wire


def reward_packet(monID, gold_val, exp_val, exp_total, items_wire, cp_val=0):
    """The s2c rewardPlayer that replaces addGoldXP: currency + the pending drops. CP is sent as
    0 for now — kills-grant-CP (class-point progression) is a separate flagged finding."""
    return {"Cmd": "rewardPlayer",
            "CP": {"val": cp_val}, "Exp": {"val": exp_val}, "ExpTotal": int(exp_total),
            "typ": "m", "monID": str(monID or 0), "factionID": -1,
            "Gold": {"val": gold_val}, "items": items_wire,
            "autoDiscarded": [], "showDropWindow": bool(items_wire), "showGold": True}


def _grant(conn, char_id, entry):
    """Move one pending entry into the real inventory; return its addItems wire item (LootID -1)."""
    cid = game._grant_item(conn, char_id, entry["raw"])
    ci = conn.execute("SELECT * FROM char_items WHERE char_item_id=?", (cid,)).fetchone()
    item = game._wire_item(conn, ci)
    item["LootID"] = -1
    return item


def take(conn, char_id, uid, item_id, loot_id):
    """getDrop: keep one specific pending drop. Returns (addItems_pkt, getLoot_pkt); the getLoot
    carries bSuccess=False (and no addItems) if nothing matched, so the client always unblocks."""
    item_id, loot_id = int(item_id), int(loot_id)
    bag = _pending.get(uid, [])
    for i, e in enumerate(bag):
        if e["loot_id"] == loot_id and e["item_id"] == item_id:
            wire = _grant(conn, char_id, e)
            bag.pop(i)
            return ({"Cmd": "addItems", "items": [wire]},
                    {"Cmd": "getLoot", "bSuccess": True, "ItemID": item_id, "LootID": loot_id})
    return (None, {"Cmd": "getLoot", "bSuccess": False, "ItemID": item_id, "LootID": loot_id})


def take_all(conn, char_id, uid):
    """bulkOperation IsLootAll: keep every pending drop. Returns (addItems_pkt, bulkOperation_pkt
    with the consumedLoot list the client clears the window by)."""
    bag = _pending.get(uid, [])
    items, consumed = [], []
    for e in bag:
        items.append(_grant(conn, char_id, e))
        c = dict(e["raw"])
        c["LootID"] = e["loot_id"]
        c["Quantity"] = e["quantity"]
        consumed.append(c)
    _pending[uid] = []
    return ({"Cmd": "addItems", "items": items},
            {"Cmd": "bulkOperation", "Success": True, "IsLootAll": True, "consumedLoot": consumed})


def discard_all(uid):
    """Discard All: drop every pending entry WITHOUT granting it. The discard packet wasn't
    captured, so this is best-effort (flagged)."""
    _pending[uid] = []
    return {"Cmd": "bulkOperation", "Success": True, "IsLootAll": False, "consumedLoot": []}


def clear(uid):
    """Forget a player's pending loot (on disconnect)."""
    _pending.pop(uid, None)


def pending(uid):
    return list(_pending.get(uid, []))
