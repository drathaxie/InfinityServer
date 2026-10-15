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
  c2s dustPattern [CharPatternID,LootID]             -> s2c dust           (dust/delete a gem)
  c2s removePattern [CharPatternID]                -> s2c removePattern   (destroy a loose gem)

Pattern fields (Pattern.cs): Level, Quality(5=Common..10=Mythic), EquipSpot,
STR/END/DEX/INT/WIS/LUK, Wild, Base, Power, Name. Weapons use Base/Wild for their
damage range (GetMin/MaxWeaponRange = Base*(1-+Wild)); gear uses the stat bonuses.
"""
import json
import random

import db
import gem_balance

# EquipSpots enum (decomp): Weapon=2, Head=3, Back=4, Armor=7
WEAPON, HEAD, BACK, ARMOR = 2, 3, 4, 7
ENHANCEABLE = {WEAPON, HEAD, BACK, ARMOR}

# The captured default "Common Weapon" gem (live-AE UpdatePattern, packets.jsonl line 61958):
# a Lvl-5 Common (Quality 5) weapon gem. V2 retains this anchor at level 5. Base/Wild give
# the 27-34 damage range (31*0.9..31*1.1, matching the in-game "27-34 DAMAGE" tooltip); the six
# stats feed the secondary stats. Only the Lvl-5 point is captured, so scaling Base/stats to
# other levels is OUR model (flagged) — the L5 shape itself is 1=1.
COMMON_WEAPON_L5 = {"ID": 1, "Quality": 5, "Level": 5,
                    "STR": 6, "END": 2, "DEX": 4, "INT": 6, "WIS": 4, "LUK": 2,
                    "Wild": 0.1, "EquipSpot": WEAPON, "Base": 31,
                    "Name": "Common Weapon", "Power": 24}
# Helm (Head) gems grant flat HEALTH instead of a damage Base (capture tooltip: "17 HEALTH").
HELM_HP = 17

# --- Authored v2 drop balance (see gem_balance.py and docs/GEM_BALANCE.md) -----------------
# Rarity chances remain the existing server policy; they are not verified official odds.
QUALITY_NAME = gem_balance.QUALITY_NAME
_QUALITY_WEIGHTS = [(5, 0.55), (6, 0.25), (7, 0.12), (8, 0.05), (9, 0.025), (10, 0.005)]
# The client treats Dust as server-authored Pattern data and only displays the authoritative
# Gained/Total values returned by ResponseDust. The captured catalog patterns predate that field,
# so InfinityServer uses a simple rarity ladder for both old and newly rolled gems.
_DUST_BY_QUALITY = {5: 5, 6: 10, 7: 20, 8: 40, 9: 80, 10: 160}

# Family labels share six favored stats, but have deliberate supporting-stat profiles.
_ARCHETYPE_PRIMARY = {family: profile[0] for family, profile in gem_balance.PROFILES.items()}
ARCHETYPES = ("warrior", "wizard", "rogue", "healer", "lucky", "hybrid")

ITEMTYPE_GEM = 43                               # enhancement gem items (Fighter Helm Gem, ...)
_SLOT_WORDS = {"weapon": WEAPON, "helm": HEAD, "head": HEAD,
               "cape": BACK, "back": BACK, "armor": ARMOR}


def is_gem_item(item):
    """Whether a catalog item is an enhancement GEM token (ItemType 43) — granted to the loose
    gem bag rather than the item inventory."""
    return int((item or {}).get("ItemType", item.get("item_type", 0) or 0) or 0) == ITEMTYPE_GEM


def gem_item_pattern(item, quality=None, level=None):
    """Roll a bag gem from a gem ITEM: the archetype + target slot come from its name
    ('Wizard Armor Gem' -> a wizard gem for the Armor slot), the rarity is rolled (or forced)."""
    name = (item or {}).get("Name") or (item or {}).get("name") or ""
    arch = archetype_of(name) or random.choice(ARCHETYPES)
    low = name.lower()
    spot = next((s for w, s in _SLOT_WORDS.items() if w in low), WEAPON)
    return roll_pattern({"EquipSpot": spot, "Name": name, "Level": item.get('Level', 1)},
                        archetype=arch, quality=quality, level=level)


def archetype_of(name):
    """Infer a gem's archetype from an item/gem name ('Wizard Armor Gem' -> 'wizard'); None if
    no known family word is present."""
    low = (name or "").lower()
    for word, _stat in _ARCHETYPE_PRIMARY.items():
        if word in low:
            return word
    return None


def roll_quality():
    """Pick a random gem Quality (5 Common .. 10 Mythic) on the weighted rarity curve."""
    r = random.random()
    acc = 0.0
    for q, w in _QUALITY_WEIGHTS:
        acc += w
        if r < acc:
            return q
    return 5


def roll_pattern(item, archetype=None, quality=None, level=None):
    """Generate a v2 budgeted gem. Explicit encounter/owner level overrides item level."""
    if not is_enhanceable(item):
        return None
    spot = int(item.get("EquipSpot", 0) or 0)
    q = max(5, min(10, int(quality))) if quality else roll_quality()
    arch = (archetype or "").lower() if archetype else random.choice(ARCHETYPES)
    return gem_balance.roll(item.get('Level', 1) if level is None else level,
                            spot, q, arch, rng=random)


def is_enhanceable(item):
    return int(item.get("EquipSpot", 0) or 0) in ENHANCEABLE


def default_pattern(item, level=None):
    """Deterministic free Common gem using the same slot/level budget as random gems."""
    spot = int(item.get("EquipSpot", 0) or 0)
    return gem_balance.default(item.get('Level', 1) if level is None else level, spot)


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
    if not is_enhanceable(item):
        return _update_pattern(item_id, None, ok=False, err="This item cannot hold a gem.")
    ci = conn.execute(
        "SELECT char_item_id,pattern_json FROM char_items WHERE char_id=? AND item_id=? AND banked=0 "
        "ORDER BY char_item_id LIMIT 1", (char_id, item_id)).fetchone()
    if ci is None:
        return _update_pattern(item_id, None, ok=False, err="Item not found.")
    existing = _parse_pattern(ci['pattern_json'])
    if existing is not None:
        return _update_pattern(item_id, existing)
    if ci['pattern_json'] is not None:
        return _update_pattern(item_id, None, ok=False, err="Existing gem data needs repair.")
    owner = conn.execute('SELECT level FROM characters WHERE id=?', (char_id,)).fetchone()
    pat = default_pattern(item, level=owner['level'])
    conn.execute(
        "UPDATE char_items SET pattern_json=?, char_pattern_id=char_item_id "
        "WHERE char_item_id=? AND pattern_json IS NULL", (json.dumps(pat), ci["char_item_id"]))
    # A concurrent power-up/equip wins without being overwritten.
    current = conn.execute('SELECT pattern_json FROM char_items WHERE char_item_id=?', (ci['char_item_id'],)).fetchone()
    pat = _parse_pattern(current['pattern_json'])
    conn.commit()
    return _update_pattern(item_id, pat)


# --- the loose gem bag (char_patterns / initPlayer.patterns[]) -----------------------------

def _next_pattern_id(conn):
    """Allocate a globally-unique instance id (shared with char_items so a CharPatternID never
    collides with a CharItemID)."""
    import game
    return game._next_char_item_id(conn)


def grant_gem(conn, char_id, pattern):
    """Drop a rolled gem into a character's loose gem bag. Returns its CharPatternID."""
    cpid = _next_pattern_id(conn)
    conn.execute("INSERT INTO char_patterns(char_pattern_id, char_id, pattern_json) VALUES(?,?,?)",
                 (cpid, int(char_id), json.dumps(pattern)))
    return cpid


def loose_gems(conn, char_id):
    """The character's unslotted gems -> initPlayer.patterns[] shape ({CharPatternID, pattern}).
    Applied gems live on the gear (char_items.pattern_json) and are NOT listed here."""
    out = []
    for r in conn.execute("SELECT char_pattern_id, pattern_json FROM char_patterns WHERE char_id=? "
                          "ORDER BY char_pattern_id", (int(char_id),)):
        try:
            out.append({"CharPatternID": r["char_pattern_id"], "pattern": json.loads(r["pattern_json"])})
        except (TypeError, ValueError):
            continue
    return out


def equip_pattern(conn, char_id, item_id, char_pattern_id, loot_id=-1):
    """Handle c2s equipPattern[selectedItem.ID, CharPatternID, LootID]: slot a LOOSE gem from the
    bag onto an owned gear item. PatternPreview sends the gear's CATALOG id (not a char_item_id)
    + the bag gem's CharPatternID. Validates the gem's EquipSpot matches the gear's slot, applies
    it (char_items.pattern_json), removes it from the bag, and bounces any gem already on that
    gear back to the bag (lossless swap)."""
    gem = conn.execute("SELECT pattern_json FROM char_patterns WHERE char_pattern_id=? AND char_id=?",
                       (int(char_pattern_id), char_id)).fetchone()
    if gem is None:
        return _update_pattern(item_id, None, ok=False, err="Gem not in your bag.")
    pat = _parse_pattern(gem["pattern_json"])
    if pat is None:
        return _update_pattern(item_id, None, ok=False, err="Bad gem.")
    owner = conn.execute('SELECT level FROM characters WHERE id=?', (char_id,)).fetchone()
    if pat.get('BalanceVersion') == gem_balance.VERSION and (owner is None or int(pat['Level']) > int(owner['level'] or 1)):
        return _update_pattern(item_id, None, ok=False, err="Your level is too low for this gem.")
    ci = conn.execute(
        "SELECT ci.char_item_id, ci.pattern_json, it.equip_spot AS spot "
        "FROM char_items ci JOIN items it ON it.id=ci.item_id "
        "WHERE ci.char_id=? AND ci.item_id=? AND ci.banked=0 "
        "ORDER BY ci.equipped DESC, ci.char_item_id LIMIT 1", (char_id, int(item_id))).fetchone()
    if ci is None:
        return _update_pattern(item_id, None, ok=False, err="Item not found.")
    gem_spot = int(pat.get("EquipSpot", 0) or 0)
    if gem_spot and int(ci["spot"] or 0) and gem_spot != int(ci["spot"]):
        return _update_pattern(item_id, None, ok=False,
                               err="That gem doesn't fit this item's slot.")
    bounced = None
    if ci["pattern_json"]:                          # bounce the gem already on this gear to the bag
        old = _parse_pattern(ci["pattern_json"])
        if old is not None:
            old_id = grant_gem(conn, char_id, old)
            bounced = {"CharPatternID": old_id, "pattern": old, "LootID": -1}
    conn.execute("UPDATE char_items SET pattern_json=?, char_pattern_id=? WHERE char_item_id=?",
                 (json.dumps(pat), int(char_pattern_id), ci["char_item_id"]))
    conn.execute("DELETE FROM char_patterns WHERE char_pattern_id=?", (int(char_pattern_id),))
    conn.commit()
    resp = _update_pattern(int(item_id), pat)
    # Internal transport metadata. The handler removes these before sending UpdatePattern, then
    # mirrors the server-side bag mutation to the client: consume the used gem and, on a swap,
    # add the displaced gem back as a new PatternItem.
    resp["_consumedPatternID"] = int(char_pattern_id)
    resp["_bouncedPatternItem"] = bounced
    return resp


def remove_pattern(conn, char_id, char_pattern_id):
    """Handle c2s removePattern from PatternPreview.DeleteClicked: destroy a LOOSE gem.

    The request carries a PatternItem.CharPatternID from ``initPlayer.patterns``. Applied gear
    is not in that collection and the stock client has no request that uses removePattern to
    strip gear, so treating this as a gear lookup left the database gem behind after the UI
    removed it.
    """
    conn.execute("DELETE FROM char_patterns WHERE char_id=? AND char_pattern_id=?",
                 (char_id, int(char_pattern_id)))
    conn.commit()
    return {"Cmd": "removePattern", "CharPatternID": int(char_pattern_id)}


# --- Gem traits (2026-09-17 client) -------------------------------------------------------
# A trait is a catalog item of type iType.Trait, sold from a trait shop and applied INTO a gem's
# slot budget: Pattern.MaxSlots is the budget, each trait's Cost is what it spends, and
# Pattern.Traits carries the applied TraitData the client renders ("Traits (2/3): ...").
# The client pays for a trait with a token item (ShopItem.TokID/TokQty), not gold -- ShopItem
# .hasRequirement() skips its money check for a trait. shop_items has no token columns yet, so
# no trait can carry a price here; a priced trait needs that schema before it can be authored.
TRAIT_ITEM_TYPE = 45
DEFAULT_TRAIT_SLOTS = 3


def trait_list(pattern):
    """The TraitData already applied to a gem."""
    return list((pattern or {}).get("Traits") or [])


def slots_used(pattern):
    """Slots the applied traits spend. Derived rather than trusted: SlotsUsed rides the wire, so
    recomputing keeps a hand-edited or stale pattern from over-spending its budget."""
    return sum(max(0, int(t.get("Cost") or 0)) for t in trait_list(pattern))


def max_slots(pattern):
    return int((pattern or {}).get("MaxSlots") or DEFAULT_TRAIT_SLOTS)


def _sync_trait_counts(pattern):
    """Keep the two wire fields the client reads consistent with Traits."""
    pattern["Traits"] = trait_list(pattern)
    pattern["MaxSlots"] = max_slots(pattern)
    pattern["SlotsUsed"] = slots_used(pattern)
    return pattern


def trait_data(item, slot_cost):
    """A catalog trait item + its shop slot cost -> the TraitData the client stores on the gem."""
    return {"ID": int(item.get("ID") or 0), "Name": item.get("Name") or "",
            "Cost": max(1, int(slot_cost or 1)), "Desc": item.get("Description") or "",
            "Lvl": int(item.get("Level") or 0)}


def _locate_gem(conn, char_id, gear_item_id, char_pattern_id):
    """Resolve the gem a trait request targets -> (kind, key, pattern) or (None, None, None).

    PatternPreview sends exactly one of the two and -1 for the other: a loose bag gem as its
    CharPatternID, or a gear item as its CATALOG id (the same convention equipPattern uses).
    Both are accepted from either field so a CharPatternID that arrives in the gear slot still
    resolves."""
    try:
        gear_item_id = int(gear_item_id)
    except (TypeError, ValueError):
        gear_item_id = -1
    try:
        char_pattern_id = int(char_pattern_id)
    except (TypeError, ValueError):
        char_pattern_id = -1

    for cpid in (char_pattern_id, gear_item_id):
        if cpid is None or cpid <= 0:
            continue
        bag = conn.execute(
            "SELECT pattern_json FROM char_patterns WHERE char_pattern_id=? AND char_id=?",
            (cpid, char_id)).fetchone()
        if bag is not None:
            pat = _parse_pattern(bag["pattern_json"])
            if pat is not None:
                return "bag", cpid, pat
        worn = conn.execute(
            "SELECT char_item_id, item_id, pattern_json FROM char_items "
            "WHERE char_id=? AND char_pattern_id=? AND banked=0", (char_id, cpid)).fetchone()
        if worn is not None and worn["pattern_json"]:
            pat = _parse_pattern(worn["pattern_json"])
            if pat is not None:
                return "gear", worn, pat

    if gear_item_id > 0:
        ci = conn.execute(
            "SELECT char_item_id, item_id, pattern_json FROM char_items "
            "WHERE char_id=? AND item_id=? AND banked=0 "
            "ORDER BY equipped DESC, char_item_id LIMIT 1", (char_id, gear_item_id)).fetchone()
        if ci is not None and ci["pattern_json"]:
            pat = _parse_pattern(ci["pattern_json"])
            if pat is not None:
                return "gear", ci, pat
    return None, None, None


def _persist_gem(conn, kind, key, pattern):
    if kind == "bag":
        conn.execute("UPDATE char_patterns SET pattern_json=? WHERE char_pattern_id=?",
                     (json.dumps(pattern), int(key)))
    else:
        conn.execute("UPDATE char_items SET pattern_json=? WHERE char_item_id=?",
                     (json.dumps(pattern), int(key["char_item_id"])))
    conn.commit()


def _trait_response(kind, key, pattern, ok=True, err=""):
    """UpdatePattern for a gear target. A loose bag gem has no s2c refresh of its own -- the
    client's Inventory.UpdatePattern only looks items up by gear/bank id -- so the handler
    mirrors a bag change with addItems.patternItems instead."""
    item_id = int(key["item_id"]) if (ok and kind == "gear") else 0
    resp = _update_pattern(item_id, pattern if ok else None, ok=ok, err=err)
    resp["silent"] = True          # the panel already closed itself; do not re-open a preview
    if ok:
        resp["_target"] = kind
        resp["_key"] = key if kind == "bag" else int(key["char_item_id"])
    return resp


def apply_trait(conn, char_id, shop_id, shop_item_id, gear_item_id, char_pattern_id):
    """c2s applyTrait[ShopID, ShopItemID, CharItemID, CharPatternID]: spend part of a gem's slot
    budget on a trait bought from a trait shop. Server-authoritative: the trait must really be
    sold by that shop, must be a trait item, must not already be on the gem, and must fit."""
    try:
        shop_id, shop_item_id = int(shop_id), int(shop_item_id)
    except (TypeError, ValueError):
        return _trait_response(None, None, None, ok=False, err="Bad trait.")
    row = conn.execute(
        "SELECT item_id, cost FROM shop_items WHERE shop_id=? AND shop_item_id=?",
        (shop_id, shop_item_id)).fetchone()
    if row is None:
        return _trait_response(None, None, None, ok=False, err="That trait is not sold here.")
    item = _item_def(conn, row["item_id"])
    if item is None or int(item.get("ItemType") or 0) != TRAIT_ITEM_TYPE:
        return _trait_response(None, None, None, ok=False, err="That is not a trait.")

    kind, key, pat = _locate_gem(conn, char_id, gear_item_id, char_pattern_id)
    if pat is None:
        return _trait_response(None, None, None, ok=False, err="Gem not found.")

    trait = trait_data(item, row["cost"])
    if any(int(t.get("ID") or 0) == trait["ID"] for t in trait_list(pat)):
        return _trait_response(None, None, None, ok=False,
                               err="This gem already has this trait.")
    used, budget = slots_used(pat), max_slots(pat)
    if budget - used < trait["Cost"]:
        return _trait_response(
            None, None, None, ok=False,
            err="This gem needs %d open slots (%d/%d used)." % (trait["Cost"], used, budget))

    pat["Traits"] = trait_list(pat) + [trait]
    _sync_trait_counts(pat)
    _persist_gem(conn, kind, key, pat)
    return _trait_response(kind, key, pat)


def remove_trait(conn, char_id, trait_item_id, gear_item_id, char_pattern_id):
    """c2s removeTrait[TraitItemID, CharItemID, CharPatternID]: strip one trait and give its
    slots back. The trait is consumed rather than refunded, matching the client confirmation,
    which promises the slots back and not the trait."""
    try:
        trait_item_id = int(trait_item_id)
    except (TypeError, ValueError):
        return _trait_response(None, None, None, ok=False, err="Bad trait.")
    kind, key, pat = _locate_gem(conn, char_id, gear_item_id, char_pattern_id)
    if pat is None:
        return _trait_response(None, None, None, ok=False, err="Gem not found.")
    before = trait_list(pat)
    kept = [t for t in before if int(t.get("ID") or 0) != trait_item_id]
    if len(kept) == len(before):
        return _trait_response(None, None, None, ok=False,
                               err="That trait is not on this gem.")
    pat["Traits"] = kept
    _sync_trait_counts(pat)
    _persist_gem(conn, kind, key, pat)
    return _trait_response(kind, key, pat)


def dust_value(pattern):
    """Dust awarded for destroying a gem. Prefer server/catalog-authored Dust, while giving
    older persisted gems (created before Dust was stored) the same rarity-based value."""
    pat = pattern or {}
    if pat.get('BalanceVersion') == gem_balance.VERSION and 'Dust' in pat:
        return max(0, int(pat['Dust']))
    explicit = int(pat.get("Dust", 0) or 0)
    if explicit > 0:
        return explicit
    quality = max(5, min(10, int(pat.get("Quality", 5) or 5)))
    return _DUST_BY_QUALITY[quality]


def _dust_response(conn, char_id, char_pattern_id, pattern=None, success=False, message=""):
    gained = dust_value(pattern) if success else 0
    if success:
        conn.execute("UPDATE characters SET dust=dust+? WHERE id=?", (gained, int(char_id)))
    row = conn.execute("SELECT dust FROM characters WHERE id=?", (int(char_id),)).fetchone()
    return {"Cmd": "dust", "bSuccess": bool(success), "Gained": gained,
            "Total": int(row["dust"] if row is not None else 0),
            "CharPatternID": int(char_pattern_id), "message": message}


def dust_pattern(conn, char_id, char_pattern_id):
    """Destroy one owned loose PatternItem and award its Dust atomically.

    A missing or foreign id fails without awarding currency, making retries safe.
    """
    cpid = int(char_pattern_id)
    row = conn.execute(
        "SELECT pattern_json FROM char_patterns WHERE char_id=? AND char_pattern_id=?",
        (int(char_id), cpid)).fetchone()
    pat = _parse_pattern(row["pattern_json"]) if row is not None else None
    if pat is None:
        return _dust_response(conn, char_id, cpid, success=False,
                              message="That gem is no longer available.")
    conn.execute("DELETE FROM char_patterns WHERE char_id=? AND char_pattern_id=?",
                 (int(char_id), cpid))
    resp = _dust_response(conn, char_id, cpid, pattern=pat, success=True)
    conn.commit()
    return resp


def dust_loot_pattern(conn, char_id, char_pattern_id, pattern):
    """Award Dust for a pending-loot gem removed by loot.dust_pending_pattern()."""
    resp = _dust_response(conn, char_id, char_pattern_id, pattern=pattern, success=True)
    conn.commit()
    return resp


def _parse_pattern(pj):
    try:
        return json.loads(pj) if pj else None
    except (TypeError, ValueError):
        return None
