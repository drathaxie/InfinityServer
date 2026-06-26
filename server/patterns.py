"""
Gems / enhancements = Patterns.

In AQW Infinity an *enhanceable* item (Weapon/Armor/Cape/Helm) must carry a Pattern
(a gem) before it can be equipped. The client (ItemPreviewNew) shows "Power Up" instead
of "Equip" while an item's ItemPattern is null, and on click sends c2s
`itemdefaultpattern [itemID]`. The server mints a default gem, applies it to the owned
instance (persisted on char_items.pattern_json so it stays empowered across relogs), and
replies s2c `UpdatePattern` (ResponseUpdatePattern) — which sets item.ItemPattern, making
the item equippable.

Wire (decomp):
  c2s itemdefaultpattern [itemID]                  -> s2c UpdatePattern {Success,ItemID,pattern,stats}
  c2s equipPattern [CharItemID,CharPatternID,LootID]-> s2c UpdatePattern  (apply a chosen gem)
  c2s removePattern [CharPatternID]                -> s2c removePattern   (clear a gem)

Pattern fields (Pattern.cs): Level, Quality(5=Common..10=Mythic), EquipSpot,
STR/END/DEX/INT/WIS/LUK, Wild, Base, Power, Name. Weapons use Base/Wild for their
damage range (GetMin/MaxWeaponRange = Base*(1-+Wild)); gear uses the stat bonuses.
"""
import json

import db

# EquipSpots enum (decomp): Weapon=2, Head=3, Back=4, Armor=7
WEAPON, HEAD, BACK, ARMOR = 2, 3, 4, 7
ENHANCEABLE = {WEAPON, HEAD, BACK, ARMOR}

# The captured default "Common Weapon" gem (live-AE UpdatePattern, packets.jsonl line 61958):
# a Lvl-5 Common (Quality 5) weapon gem. This is exactly what "Power Up" mints. Base/Wild give
# the 27-34 damage range (31*0.9..31*1.1, matching the in-game "27-34 DAMAGE" tooltip); the six
# stats feed the secondary stats. Only the Lvl-5 point is captured, so scaling Base/stats to
# other levels is OUR model (flagged) — the L5 shape itself is 1=1.
COMMON_WEAPON_L5 = {"ID": 1, "Quality": 5, "Level": 5,
                    "STR": 6, "END": 2, "DEX": 4, "INT": 6, "WIS": 4, "LUK": 2,
                    "Wild": 0.1, "EquipSpot": WEAPON, "Base": 31,
                    "Name": "Common Weapon", "Power": 24}
# Helm (Head) gems grant flat HEALTH instead of a damage Base (capture tooltip: "17 HEALTH").
HELM_HP = 17


def is_enhanceable(item):
    return int(item.get("EquipSpot", 0) or 0) in ENHANCEABLE


def default_pattern(item):
    """The default Common gem that 'Power Up' mints. The capture is unambiguous: itemdefaultpattern
    for items 300, 17873 AND 51605 — three DIFFERENT weapons — ALL returned the SAME Lvl-5 Common
    Weapon gem (Base 31). So Power Up mints a FIXED Common gem, NOT one scaled to the item's level
    (an earlier level-scaling was wrong — a Lvl-1 item like Blade of Awe got Base 6 -> ~7 damage).
    Weapons get the captured Common Weapon shape; a Helm grants flat HEALTH; other gear gets a
    Common gear gem (no weapon Base — gear-gem Power Up wasn't captured, so its stats are flagged)."""
    spot = int(item.get("EquipSpot", 0) or 0)
    if spot == WEAPON:
        return dict(COMMON_WEAPON_L5)
    pat = {"ID": 1, "Level": 5, "Quality": 5, "EquipSpot": spot,
           "STR": 3, "END": 3, "DEX": 0, "INT": 0, "WIS": 0, "LUK": 0,
           "Wild": 0.0, "Base": 0, "Power": 15, "Name": "Standard", "HP": 0}
    if spot == HEAD:
        pat["HP"] = HELM_HP
    return pat


def weapon_range(pat):
    """The weapon damage range (DmgMin, DmgMax) a weapon gem confers: Base*(1-Wild)..Base*(1+Wild)
    (Pattern.GetMin/MaxWeaponRange). None for a gem with no Base (gear gems). CONFIRMED exact vs
    capture: Base 31, Wild 0.1 -> 27..34 (the in-game "27-34 DAMAGE" tooltip). AE TRUNCATES (the
    min is 27, not round(27.9)=28), so floor both ends."""
    base = float(pat.get("Base") or 0)
    wild = float(pat.get("Wild") or 0)
    if base <= 0:
        return None
    return (max(1, int(base * (1 - wild))), max(1, int(base * (1 + wild))))


def primary_stats(pat):
    """The six primary stats a gem contributes, mapped to the `sta` keys (the gem stores LUK;
    the stat block calls it LCK)."""
    return {"STR": int(pat.get("STR", 0) or 0), "END": int(pat.get("END", 0) or 0),
            "DEX": int(pat.get("DEX", 0) or 0), "INT": int(pat.get("INT", 0) or 0),
            "WIS": int(pat.get("WIS", 0) or 0),
            "LCK": int(pat.get("LUK", pat.get("LCK", 0)) or 0)}


def flat_hp(pat):
    """Flat HP a gem confers (Helm/health gems carry an HP field; 0 otherwise)."""
    return int(pat.get("HP", 0) or 0)


def applied(ci):
    """The Pattern (gem) applied to an owned item row, or None."""
    keys = ci.keys() if hasattr(ci, "keys") else ()
    pj = ci["pattern_json"] if "pattern_json" in keys else None
    try:
        return json.loads(pj) if pj else None
    except (TypeError, ValueError):
        return None


def _item_def(conn, item_id):
    return db.item(conn, item_id)


def _update_pattern(item_id, pattern, ok=True, err=""):
    return {"Cmd": "UpdatePattern", "Success": ok, "errorMessage": err,
            "ItemID": int(item_id), "pattern": pattern, "stats": None}


def item_default_pattern(conn, char_id, item_id):
    """Handle c2s itemdefaultpattern: mint + apply a default gem to the owned item and
    return the UpdatePattern that makes it equippable. Persisted on char_items."""
    try:
        item_id = int(item_id)
    except (TypeError, ValueError):
        return _update_pattern(0, None, ok=False, err="Bad item.")
    item = _item_def(conn, item_id)
    if item is None:
        return _update_pattern(item_id, None, ok=False, err="Unknown item.")
    pat = default_pattern(item)
    ci = conn.execute(
        "SELECT char_item_id FROM char_items WHERE char_id=? AND item_id=? AND banked=0 "
        "ORDER BY char_item_id LIMIT 1", (char_id, item_id)).fetchone()
    if ci is not None:
        conn.execute(
            "UPDATE char_items SET pattern_json=?, char_pattern_id=char_item_id "
            "WHERE char_item_id=?", (json.dumps(pat), ci["char_item_id"]))
        conn.commit()
    return _update_pattern(item_id, pat)


def equip_pattern(conn, char_id, item_id, char_pattern_id, loot_id=-1):
    """Handle c2s equipPattern[selectedItem.ID, CharPatternID, LootID]. PatternPreview.cs:297
    sends `RequestEquipPattern(selectedItem.ID, pItem.CharPatternID, ...)` — the first arg is the
    item's CATALOG ID (despite the request field being named CharItemID), NOT a char_item_id. So
    resolve the owned instance of that item (preferring the equipped one) and apply the gem.

    NOTE: a per-player GEM inventory (initPlayer.patterns[] -> a char_patterns table) isn't
    modelled yet, so CharPatternID can't be resolved to a distinct gem's stats — we apply the
    standard Common gem (same as Power Up). FLAGGED: distinct owned-gem stats are a later finding."""
    ci = conn.execute(
        "SELECT * FROM char_items WHERE char_id=? AND item_id=? AND banked=0 "
        "ORDER BY equipped DESC, char_item_id LIMIT 1", (char_id, int(item_id))).fetchone()
    if ci is None:
        return _update_pattern(item_id, None, ok=False, err="Item not found.")
    item = _item_def(conn, ci["item_id"]) or {}
    pat = default_pattern(item)
    conn.execute(
        "UPDATE char_items SET pattern_json=?, char_pattern_id=? WHERE char_item_id=?",
        (json.dumps(pat), char_pattern_id or ci["char_item_id"], ci["char_item_id"]))
    conn.commit()
    return _update_pattern(ci["item_id"], pat)


def remove_pattern(conn, char_id, char_pattern_id):
    """Handle c2s removePattern: clear the gem from whichever item holds it."""
    conn.execute(
        "UPDATE char_items SET pattern_json=NULL WHERE char_id=? AND char_pattern_id=?",
        (char_id, char_pattern_id))
    conn.commit()
    return {"Cmd": "removePattern", "CharPatternID": int(char_pattern_id)}
