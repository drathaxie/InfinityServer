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

# --- Random-rarity drop gems (the AE loot model) ------------------------------------------
# Captured rewardPlayer drops (packets.jsonl) each carry an ItemPattern rolled at a RANDOM
# Quality (the rarity) with Base/Power scaling by quality and randomised stats:
#   Q5 Common   Fighter Helm  Base 31  Power 26   (STR4/END7/DEX4/INT1/WIS4/LUK6)
#   Q6 Uncommon Wizard Armor  Base 68  Power 30   (STR1/END4/DEX5/INT5/WIS10/LUK5)
#   Q7 Rare     Thief Armor   Base 76  Power 35   (STR5/END4/DEX10/INT2/WIS8/LUK6)
# so a good drop = a high-Quality gem = a much bigger weapon Base + stats. Q5-6 Base/Power are
# capture-anchored; Q7 real drop was Base 76 (kept a touch higher for a clean ladder), Q8-10 are
# OUR extrapolation (flagged) since the capture only reached Rare. Weights make rarity climb rare.
QUALITY_NAME = {5: "Common", 6: "Uncommon", 7: "Rare", 8: "Epic", 9: "Legendary", 10: "Mythic"}
_QUALITY_WEIGHTS = [(5, 0.55), (6, 0.25), (7, 0.12), (8, 0.05), (9, 0.025), (10, 0.005)]
_BASE_BY_QUALITY = {5: 31, 6: 68, 7: 95, 8: 130, 9: 175, 10: 230}
_POWER_BY_QUALITY = {5: 26, 6: 32, 7: 40, 8: 50, 9: 63, 10: 80}
# The client treats Dust as server-authored Pattern data and only displays the authoritative
# Gained/Total values returned by ResponseDust. The captured catalog patterns predate that field,
# so InfinityServer uses a simple rarity ladder for both old and newly rolled gems.
_DUST_BY_QUALITY = {5: 5, 6: 10, 7: 20, 8: 40, 9: 80, 10: 160}

# Gems are CLASS-BASED: the archetype decides which primary stat the gem pumps (a Warrior gem
# raises STR, a Wizard gem INT, ...). The archetype's primary stat gets the big rarity-scaled
# roll; the other five roll a small "spread". Archetype names match AQW's enhancement families
# (and the captured gem Names: Fighter/Wizard/Thief/Healer). `default` = a balanced gem.
_ARCHETYPE_PRIMARY = {
    "warrior": "STR", "fighter": "STR", "brute": "STR",
    "wizard": "INT", "mage": "INT", "sorcerer": "INT",
    "rogue": "DEX", "thief": "DEX", "assassin": "DEX",
    "healer": "WIS", "cleric": "WIS",
    "lucky": "LUK", "hybrid": "END",
}
ARCHETYPES = ("warrior", "wizard", "rogue", "healer", "lucky", "hybrid")

ITEMTYPE_GEM = 43                               # enhancement gem items (Fighter Helm Gem, ...)
_SLOT_WORDS = {"weapon": WEAPON, "helm": HEAD, "head": HEAD,
               "cape": BACK, "back": BACK, "armor": ARMOR}


def is_gem_item(item):
    """Whether a catalog item is an enhancement GEM token (ItemType 43) — granted to the loose
    gem bag rather than the item inventory."""
    return int((item or {}).get("ItemType", item.get("item_type", 0) or 0) or 0) == ITEMTYPE_GEM


def gem_item_pattern(item, quality=None):
    """Roll a bag gem from a gem ITEM: the archetype + target slot come from its name
    ('Wizard Armor Gem' -> a wizard gem for the Armor slot), the rarity is rolled (or forced)."""
    name = (item or {}).get("Name") or (item or {}).get("name") or ""
    arch = archetype_of(name) or random.choice(ARCHETYPES)
    low = name.lower()
    spot = next((s for w, s in _SLOT_WORDS.items() if w in low), WEAPON)
    return roll_pattern({"EquipSpot": spot, "Name": name}, archetype=arch, quality=quality)


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


def roll_pattern(item, archetype=None, quality=None, level=5):
    """Roll a class-based, rarity-scaled gem (ItemPattern) for an enhanceable item — the AE drop
    model. Rarity (Quality) drives the weapon Base/Power and the overall stat magnitude; the
    ARCHETYPE decides which primary stat gets the big roll (Warrior->STR, Wizard->INT, ...), the
    rest get a small spread. `archetype`/`quality` force a family/tier (drops/dev); otherwise
    both are rolled. Returns None for a non-enhanceable item (materials/class items never gem)."""
    if not is_enhanceable(item):
        return None
    spot = int(item.get("EquipSpot", 0) or 0)
    q = max(5, min(10, int(quality))) if quality else roll_quality()
    arch = (archetype or "").lower() if archetype else random.choice(ARCHETYPES)
    primary = _ARCHETYPE_PRIMARY.get(arch, "STR")
    stats = {"STR": 0, "END": 0, "DEX": 0, "INT": 0, "WIS": 0, "LUK": 0}
    for k in stats:                                      # small spread on every stat
        stats[k] = random.randint(0, q // 2)
    stats[primary] = random.randint(q, 3 * q)            # the archetype stat: the big rarity roll
    pat = {"ID": 1, "Quality": q, "Level": int(level or 5),
           "Wild": 0.1, "EquipSpot": spot,
           "Base": _BASE_BY_QUALITY[q], "Power": _POWER_BY_QUALITY[q],
           "Dust": _DUST_BY_QUALITY[q],
           "Name": f"{QUALITY_NAME[q]} {arch.capitalize()}"}
    pat.update(stats)
    if spot == HEAD:                                      # helms also carry flat HP, scaled by rarity
        pat["HP"] = round(HELM_HP * q / 5)
    return pat


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


# --- the loose gem bag (char_patterns / initPlayer.patterns[]) -----------------------------

def _next_pattern_id(conn):
    """Allocate a globally-unique instance id (shared with char_items so a CharPatternID never
    collides with a CharItemID)."""
    cur = int(db.kv_get(conn, "next_char_item_id", "1"))
    db.kv_set(conn, "next_char_item_id", cur + 1)
    return cur


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
    ci = conn.execute(
        "SELECT ci.char_item_id, ci.pattern_json, it.equip_spot AS spot "
        "FROM char_items ci JOIN items it ON it.item_id=ci.item_id "
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


def dust_value(pattern):
    """Dust awarded for destroying a gem. Prefer server/catalog-authored Dust, while giving
    older persisted gems (created before Dust was stored) the same rarity-based value."""
    pat = pattern or {}
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
