"""
Game logic for InfinityServer: accounts, characters, persistent inventory,
and the first authoritative economy (buy/sell). All state lives in SQLite
(db.py); nothing here talks to Artix.

Design note: initPlayer / loginResponse / statUpdate are built FRESH from each
character's own DB state — no captured template underneath. Every field is either
derived from the character's columns/inventory/class or an honest neutral default;
nothing is inherited from another account's capture. (See data-sources-audit:
serve live per-character data, no capture/template band-aids.)
"""
import hashlib
import hmac
import json
import pathlib
import random
import re
import secrets
import time

import db
import seed
import patterns
import combat        # weapon-damage coefficients (WEAPON_MIN/MAX) shared with the damage roll
import forge         # build_seact: the class's skill bar + particle list (initPlayer.Actions)


# Wire field (user.customization / user.stats) -> character column. Plain ints,
# so they live as columns and are reattached to the template when building user.
_COLOR_COLS = {
    "SkinColor": "skin_color", "EyeColor": "eye_color", "HairColor": "hair_color",
    "TrimColor": "trim_color", "AccessoryColor": "accessory_color",
}
_STAT_COLS = {
    "STR": "stat_str", "END": "stat_end", "DEX": "stat_dex",
    "INT": "stat_int", "WIS": "stat_wis", "LCK": "stat_lck",
}

# Neutral default storage slots for a fresh account (the captured template carried
# drathaxie's maxed 167/390/90). Generous for a private test server; not per-account yet.
DEFAULT_BANK_SLOTS = 200
DEFAULT_BAG_SLOTS = 200
DEFAULT_HOUSE_SLOTS = 50
# PlayerInfo.dateCreated is a C# DateTime — it MUST be a parseable ISO datetime, never ""
# (an empty string aborts the whole initPlayer deserialization and hangs login).
DEFAULT_CREATED = "2024-01-01T00:00:00"
# upgradeExpires is a C# DateTime too; membership is gated on UpgradeDays>0 (Player.IsMember),
# so a free account just needs a parseable (past) date here.
DEFAULT_UPGRADE_EXPIRES = "2024-01-01T00:00:00"
# PlayerInfo.ActivationFlag is deserialized but never gated on in the client (decomp: declared
# only) — a neutral "activated" constant is safe and account-agnostic.
DEFAULT_ACTIVATION_FLAG = 1

# user-object display constants the client expects (rage bar + threshold colours). These are
# fixed client-side presentation values, identical for every player — not account data.
USER_RP_MAX = 100
USER_RP_COLOR = 16777215            # white
USER_THRESHOLD = 50
USER_THRESHOLD_COLOR = 16745728

# The full statUpdate `sta` block carries damage-type in/out multipliers besides the primaries.
# For a base character with no resistance modifiers they're all 1.0; build_combat_stats supplies
# the computed primaries/derived on top.
_STA_MULTIPLIERS = {
    "thi": 1.0, "cpo": 1.0, "cpi": 1.0, "cao": 1.0, "cai": 1.0, "cmo": 1.0,
    "cmi": 1.0, "cdo": 1.0, "cdi": 1.0, "cho": 1.0, "chi": 1.0, "cmc": 1.0,
}

# userPrefs (UI toggles) — every player starts with all on; the per-char `prefs` column
# overrides any the player has changed.
_DEFAULT_USER_PREFS = {
    "ShowHelm": True, "ShowCloak": True, "ShowPet": True, "ShowOtherCloaks": True,
    "ShowOtherPets": True, "Goto": True, "Friend": True, "Party": True,
    "Guild": True, "Whisper": True, "Duel": True,
}

# loginResponse server-wide config (news/version/max-slot caps + MOTD). Identical for every
# player — server config, not per-account capture data.
SERVER_NEWS_INFO = ("sNews=1116,sMap=news/Map-UI_r38.swf,sBook=news/spiderbook3.swf,"
                    "sAssets=Assets_20260508.swf,gMenu=dynamic-gameMenu-17Jan22.swf,"
                    "sVersion=R0039,QSInfo=519,iMaxBagSlots=500,iMaxBankSlots=900,"
                    "iMaxHouseSlots=300,iMaxGuildMembers=800,iMaxFriends=300,iMaxLoadoutSlots=50")
SERVER_MOTD = ("Staff will never ask for your password - if someone asks for your password, "
               "report them for Griefing. NEVER give out your password, no matter what.")


def _gen_colors(seed_val):
    """A distinct-but-deterministic customization colour set for a character."""
    rng = random.Random(seed_val)
    return {col: rng.randint(0, 0xFFFFFF) for col in _COLOR_COLS.values()}


def _default_hair_id(conn, gender=None):
    """The default hair for a brand-new character: the first hair in the hairs catalog matching
    the character's gender (the same list /charedit offers). 0 when the catalog isn't seeded yet
    (e.g. local dev before hairs are imported) — never a template's hair."""
    hairs = db.hairs_list(conn, gender=gender) if gender else db.hairs_list(conn)
    return int(hairs[0].get("ID", 0) or 0) if hairs else 0


# ---- accounts / characters -------------------------------------------------

# Dev/staff accounts get access_level 100 on login — the top staff tier, which unlocks ALL the
# in-game authoring tools: Dialogger cutscene editor, apop editor, charedit, /cutscene, /devon, and
# the SkillForge panel (UIMiniMenu.ToggleSkillForge gates on hasAccess(100)). The allowlist lives in
# data/dev_users.txt so it's editable without code; everyone else stays a normal player (access 0),
# keeping normal play 1=1 with AE.
DEV_ACCESS_LEVEL = 100

# Founder achievement. Despair's apop (id 161) gates her Kickstarter-reward shops on the "ip25"
# achievement, one bit per founder tier (indexes 0..10: Day One, 100% Funded, Founder, Epic,
# Underworld, Legendary, Immortalized, Benevolent, Weapon Designer, Armor Designer, Mysterious).
# 0x7FF = all 11 tiers. Dev/owner accounts get the full set so every founder shop appears.
FOUNDER_IP25 = 0x7FF
_DEV_USERS_FILE = pathlib.Path(__file__).resolve().parent.parent / "data" / "dev_users.txt"


def _dev_users():
    try:
        return {ln.strip().lower() for ln in _DEV_USERS_FILE.read_text(encoding="utf-8").splitlines()
                if ln.strip() and not ln.lstrip().startswith("#")}
    except OSError:
        return set()


def login(conn, username, password):
    """Authenticate (or register) an account by username+password; return the char row, or None
    on a wrong password. A NEW username registers an account with that password (open
    registration — OUR design, flagged: no AE capture defines account creation). An EXISTING
    account must present the matching password; legacy plaintext (or an empty password) is
    accepted once and upgraded to a hash. The matching game-server Login is gated by a session
    token (issue_token / resolve_session), not by re-sending the password."""
    # Match case-insensitively so "Drathaxie" and "drathaxie" are one account (oldest wins).
    acc = conn.execute(
        "SELECT * FROM accounts WHERE LOWER(username)=LOWER(?) ORDER BY id LIMIT 1",
        (username,)).fetchone()
    if acc is None:
        account_id = conn.execute(
            "INSERT INTO accounts(username, password, created) VALUES(?,?,?) RETURNING id",
            (username, hash_password(password), time.time()),
        ).fetchone()[0]
    else:
        account_id = acc["id"]
        stored = acc["password"]
        if not stored:                          # legacy/empty -> claim with this password
            conn.execute("UPDATE accounts SET password=? WHERE id=?",
                         (hash_password(password), account_id))
        else:
            ok, needs_upgrade = verify_password(password, stored)
            if not ok:
                return None                     # wrong password
            if needs_upgrade:                   # legacy plaintext matched -> store a hash now
                conn.execute("UPDATE accounts SET password=? WHERE id=?",
                             (hash_password(password), account_id))

    char = conn.execute(
        "SELECT * FROM characters WHERE account_id=?", (account_id,)
    ).fetchone()
    if char is None:
        char = _create_character(conn, account_id, username)
    # grant dev access to staff accounts (unlocks the authoring tools) — data/dev_users.txt
    if username.lower() in _dev_users():
        if int(char["access_level"] or 0) < DEV_ACCESS_LEVEL:
            conn.execute("UPDATE characters SET access_level=? WHERE id=?",
                         (DEV_ACCESS_LEVEL, char["id"]))
            char = conn.execute("SELECT * FROM characters WHERE id=?", (char["id"],)).fetchone()
        # ...and full founder status, so Despair's founder-reward shops are visible.
        try:
            ach = json.loads(char["achievements"] or "{}")
        except (ValueError, TypeError):
            ach = {}
        if (int(ach.get("ip25", 0)) & FOUNDER_IP25) != FOUNDER_IP25:
            ach["ip25"] = int(ach.get("ip25", 0)) | FOUNDER_IP25
            conn.execute("UPDATE characters SET achievements=? WHERE id=?",
                         (json.dumps(ach), char["id"]))
            char = conn.execute("SELECT * FROM characters WHERE id=?", (char["id"],)).fetchone()
    conn.commit()
    return char


def authenticate(conn, username, password):
    """Verify an EXISTING account's password (does NOT create accounts, unlike login) and return
    {"username","access"} or None. access = the account's character access_level, raised to
    DEV_ACCESS_LEVEL for allowlisted dev accounts (data/dev_users.txt). The staff editor login uses
    this: a normal account authenticates but returns its real (low) access, which the gate rejects."""
    if not username:
        return None
    acc = conn.execute("SELECT id, username, password FROM accounts WHERE LOWER(username)=LOWER(?) "
                       "ORDER BY id LIMIT 1", (username,)).fetchone()
    if acc is None or not acc["password"]:
        return None
    ok, _ = verify_password(password, acc["password"])
    if not ok:
        return None
    row = conn.execute("SELECT MAX(access_level) AS a FROM characters WHERE account_id=?",
                       (acc["id"],)).fetchone()
    access = int((row["a"] if row and row["a"] is not None else 0) or 0)
    if str(acc["username"]).lower() in _dev_users():
        access = max(access, DEV_ACCESS_LEVEL)
    return {"username": acc["username"], "access": access}


# ---- credentials (OUR design — no AE capture covers account auth) -----------
# PBKDF2-HMAC-SHA256 with a per-account random salt, stdlib-only. Stored as
# "pbkdf2_sha256$<iterations>$<salt_hex>$<hash_hex>".
_PBKDF2_ITER = 200_000


def hash_password(password):
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", (password or "").encode("utf-8"), salt, _PBKDF2_ITER)
    return f"pbkdf2_sha256${_PBKDF2_ITER}${salt.hex()}${dk.hex()}"


def verify_password(password, stored):
    """(ok, needs_upgrade). A hashed value is checked with a constant-time compare; a legacy
    plaintext value compares directly and is flagged for upgrade to a hash."""
    if not stored:
        return (False, False)
    if stored.startswith("pbkdf2_sha256$"):
        try:
            _, iters, salt_hex, hash_hex = stored.split("$")
            dk = hashlib.pbkdf2_hmac("sha256", (password or "").encode("utf-8"),
                                     bytes.fromhex(salt_hex), int(iters))
            return (hmac.compare_digest(dk.hex(), hash_hex), False)
        except Exception:
            return (False, False)
    return (hmac.compare_digest(password or "", stored), True)   # legacy plaintext


def issue_token(conn, account_id):
    """Mint + store a fresh session token for an account; the client carries it as account.sToken
    and the game-server Login must present it. Returns the token."""
    token = secrets.token_hex(24)
    conn.execute("UPDATE accounts SET session_token=? WHERE id=?", (token, int(account_id)))
    conn.commit()
    return token


def resolve_session(conn, username, token):
    """The char for (username, valid session token), or None — the game-server TCP Login gate, so a
    direct connection can't impersonate an account without the API-issued token (constant-time)."""
    if not token:
        return None
    acc = conn.execute(
        "SELECT * FROM accounts WHERE LOWER(username)=LOWER(?) ORDER BY id LIMIT 1",
        (username,)).fetchone()
    if acc is None or not acc["session_token"]:
        return None
    if not hmac.compare_digest(str(acc["session_token"]), str(token)):
        return None
    return conn.execute("SELECT * FROM characters WHERE account_id=?", (acc["id"],)).fetchone()


def _next_char_item_id(conn):
    cur = int(db.kv_get(conn, "next_char_item_id", "1"))
    db.kv_set(conn, "next_char_item_id", cur + 1)
    return cur


# A FRESH character starts clean — NOT cloned from the captured template (which is a maxed
# account: 20M gold, level 5, 255 items). Every new player earns their own progress: level 1,
# no gold, base stats (gems supply the rest), and the base Warrior class + a pre-gemmed Default
# Sword so they can immediately fight. There is no fresh-account capture (the captured one is
# maxed), so this starter set is OUR design choice (flagged) — Default Sword (item 1) and Warrior
# (class 33 / armor 15654) are the catalog's level-1 base.
STARTER_CLASS_ID = 33           # Warrior — a real ClassID with seeded skills + visual rig
STARTER_CLASS_ITEM = 15654      # Warrior class armor (EquipSpot 6)
STARTER_WEAPON_ITEM = 1         # Default Sword (Level 1, EquipSpot 2)


def _grant_equipped(conn, char_id, item_id):
    """Grant one catalog item to a character, equipped. Returns the CharItemID (or None)."""
    item = db.item(conn, item_id)
    if item is None:
        return None
    item.setdefault("ID", int(item_id))
    item["Equipped"] = True
    return _grant_item(conn, char_id, item)


def _create_character(conn, account_id, name):
    """Create a FRESH level-1 character: clean slate (no gold, no inherited inventory), the base
    Warrior class and a pre-gemmed Default Sword, with a distinct seeded colour set so each
    player has their own avatar."""
    gender = "M"
    char_id = conn.execute(
        "INSERT INTO characters(account_id, name, gender, gold, coins, level, exp, class_id, "
        "stat_str, stat_end, stat_dex, stat_int, stat_wis, stat_lck) "
        "VALUES(?,?,?,0,0,1,0,?,0,0,0,0,0,0) RETURNING id",
        (account_id, name, gender, STARTER_CLASS_ID),
    ).fetchone()[0]
    colors = _gen_colors(char_id)
    colors["hair_id"] = _default_hair_id(conn, gender)   # catalog default, not a template's hair
    conn.execute(
        "UPDATE characters SET skin_color=?, eye_color=?, hair_color=?, trim_color=?, "
        "accessory_color=?, hair_id=? WHERE id=?",
        (colors["skin_color"], colors["eye_color"], colors["hair_color"],
         colors["trim_color"], colors["accessory_color"], colors["hair_id"], char_id),
    )
    # the only starting gear: base class + weapon (everything else is earned via loot/shops)
    _grant_equipped(conn, char_id, STARTER_CLASS_ITEM)
    _grant_equipped(conn, char_id, STARTER_WEAPON_ITEM)
    # pre-apply the Common gem so the Default Sword is equippable + hits 27-34 (keystone)
    patterns.item_default_pattern(conn, char_id, STARTER_WEAPON_ITEM)
    return conn.execute("SELECT * FROM characters WHERE id=?", (char_id,)).fetchone()


# /charedit -> changeColor. Param order CONFIRMED from a live capture, cross-referenced against the
# labelled s2c reply:  [SkinColor, EyeColor, HairColor, BaseColor, TrimColor, AccessoryColor, HairID]
# There's no base_color column (AE's customization object carries only Skin/Eye/Hair/Trim/Accessory),
# so index 3 (BaseColor) is parsed but dropped.
_CHANGECOLOR_ORDER = ["skin_color", "eye_color", "hair_color", None, "trim_color", "accessory_color"]


def save_customization(conn, char, params):
    """Persist colours + hair from a changeColor payload onto the character row.
    Returns the dict of {column: value} actually written (for echo + logging)."""
    params = list(params or [])
    applied = {}
    for i, col in enumerate(_CHANGECOLOR_ORDER):
        if col is None or i >= len(params):
            continue
        try:
            applied[col] = int(params[i]) & 0xFFFFFF      # colours are 24-bit RGB ints
        except (TypeError, ValueError):
            pass
    # hair id is the value right after the six colours (index 6), if present
    if len(params) > 6:
        try:
            applied["hair_id"] = int(params[6])
        except (TypeError, ValueError):
            pass
    if not applied:
        return {}
    sets = ", ".join(f"{col}=?" for col in applied)
    conn.execute(f"UPDATE characters SET {sets} WHERE id=?",
                 (*applied.values(), char["id"]))
    return applied


def _item_is_class(item):
    """A class-armor item (EquipSpots.Class = 6 / isClass) — its Quantity is class POINTS
    (rank), not a stack (InventoryItem.cs:114-116)."""
    return bool(item.get("isClass")) or int(item.get("EquipSpot", 0) or 0) == 6


def _grant_item(conn, char_id, item, loot_id=-1):
    """Ensure the item is in the catalog, then add one owned instance to char_items.
    Returns the new CharItemID. A class item is granted at the maxed class points (CP), not
    a stack count — so the class is fully playable and CP stays consistent (P2-1)."""
    item_id = int(item.get("ID", 0))
    db.store_item(conn, item)               # ensure the catalog row exists (canonical columns)
    # A dropped gem (ItemPattern) is a per-instance roll — its strength is unique, so gemmed
    # gear must NEVER merge onto another row (that would clobber one roll with another's stack).
    gem = item.get("ItemPattern")
    gem_json = json.dumps(gem) if gem else None
    # Stack onto an existing non-banked row for this item (the client keys inventory by item ID,
    # so a second row collides and crashes login). Class items are the exception — their Quantity
    # is class points, not a stack — so they're granted once at maxed CP, never merged. Gemmed
    # gear is likewise never merged (unique roll per instance).
    if not _item_is_class(item) and not item.get("Equipped") and gem is None:
        existing = conn.execute(
            "SELECT char_item_id FROM char_items WHERE char_id=? AND item_id=? AND banked=0 "
            "AND pattern_json IS NULL ORDER BY char_item_id LIMIT 1", (char_id, item_id)).fetchone()
        if existing:
            conn.execute("UPDATE char_items SET quantity=quantity+? WHERE char_item_id=?",
                         (int(item.get("Quantity", 1) or 1), existing["char_item_id"]))
            return int(existing["char_item_id"])
    cid = _next_char_item_id(conn)
    qty = seed.CLASS_CP_MAX if _item_is_class(item) else int(item.get("Quantity", 1) or 1)
    conn.execute(
        "INSERT INTO char_items(char_item_id, char_id, item_id, quantity, equipped, "
        "banked, loot_id, purchase_date, pattern_json, char_pattern_id) VALUES(?,?,?,?,?,?,?,?,?,?)",
        (cid, char_id, item_id, qty,
         1 if item.get("Equipped") else 0, 0, loot_id, item.get("PurchaseDate"),
         gem_json, cid if gem_json else None))
    return cid


def _is_class_item(conn, item_id):
    """Whether a catalog item is a class armor (so sell/drop must be rejected — its Quantity
    is class points, not a sellable stack)."""
    item = db.item(conn, item_id)
    if not item:
        return False
    try:
        return _item_is_class(item)
    except Exception:
        return False


def _wire_item(conn, ci):
    """Rebuild the wire InventoryItem from the catalog def + this owned instance."""
    item = db.item(conn, ci["item_id"]) or {"ID": ci["item_id"]}
    item["CharItemID"] = ci["char_item_id"]
    item["Quantity"] = ci["quantity"]
    item["LootID"] = ci["loot_id"]
    item["Equipped"] = bool(ci["equipped"])  # so the client knows what's worn (incl. the
                                             # equipped Class item -> HUD shows the class)
    if ci["purchase_date"]:
        item["PurchaseDate"] = ci["purchase_date"]
    pat = patterns.applied(ci)               # applied gem -> item stays empowered
    if pat is not None:
        item["ItemPattern"] = pat
    return item


def inventory(conn, char_id):
    rows = conn.execute(
        "SELECT * FROM char_items WHERE char_id=? AND banked=0 ORDER BY char_item_id",
        (char_id,)).fetchall()
    # The client keys its inventory dict by item ID (ResponseInitPlayer.Execute), so two rows
    # with the same item id crash login with "same key has already been added". Merge duplicate
    # rows into one wire item (sum quantities; equipped wins) — a safety net over _grant_item's
    # stacking that also heals any duplicates already in the DB.
    merged, order = {}, []
    for r in rows:
        iid = r["item_id"]
        if iid in merged:
            merged[iid]["Quantity"] = int(merged[iid].get("Quantity", 1) or 1) + int(r["quantity"] or 1)
            if r["equipped"]:
                merged[iid]["Equipped"] = True
        else:
            merged[iid] = _wire_item(conn, r)
            order.append(iid)
    return [merged[i] for i in order]


def bank(conn, char_id):
    """The character's banked items (char_items.banked=1) as wire InventoryItems —
    ResponseLoadBank.items, which the client hands to setupBank(items)."""
    rows = conn.execute(
        "SELECT * FROM char_items WHERE char_id=? AND banked=1 ORDER BY char_item_id",
        (char_id,)).fetchall()
    items = []
    for r in rows:
        it = _wire_item(conn, r)
        it["Banked"] = True
        items.append(it)
    return items


# ---- bank moves (deposit / withdraw / swap) ---------------------------------
# Wire (decomp: Request/ResponseInvToBank, BankToInv, BankSwap):
#   c2s bankFromInv [itemID]         -> s2c {Cmd:"InvToBank",  invID}
#   c2s bankToInv   [itemID]         -> s2c {Cmd:"BankToInv",  bankID}
#   c2s bankSwapInv [invID, bankID]  -> s2c {Cmd:"BankSwap",   invID, bankID}
# The IDs are CATALOG item ids (the client keys both its items and bankedItems dicts by ID),
# and the client only mutates ON the response — so refusing a move is simply not replying
# (there is no failure packet in the protocol). ALL of an item's rows move together (gemmed +
# plain), matching the client's one-dict-entry-per-id view.

def _slots_used(conn, char_id, banked):
    """How many bank/bag SLOTS are in use = distinct item ids on that side (the client shows
    one slot per item id — its dict is keyed by ID — regardless of row count server-side)."""
    return conn.execute(
        "SELECT COUNT(DISTINCT item_id) AS n FROM char_items WHERE char_id=? AND banked=?",
        (char_id, 1 if banked else 0)).fetchone()["n"]


def _move_rows(conn, char_id, item_id, to_banked):
    """Flip banked on every row of an item on the source side. Returns rows moved (0 = not
    owned there). Refuses (0) if any source row is EQUIPPED — the client's own UI blocks
    banking equipped items ('You can not bank equipped items!'), and unequipping via the
    bank would desync the avatar; a modded client must not bypass that."""
    src = 0 if to_banked else 1
    rows = conn.execute(
        "SELECT char_item_id, equipped FROM char_items WHERE char_id=? AND item_id=? AND banked=?",
        (char_id, item_id, src)).fetchall()
    if not rows or any(r["equipped"] for r in rows):
        return 0
    conn.execute("UPDATE char_items SET banked=? WHERE char_id=? AND item_id=? AND banked=?",
                 (1 if to_banked else 0, char_id, item_id, src))
    return len(rows)


def bank_deposit(conn, char, item_id):
    """bankFromInv [itemID]: move an owned item (all its rows) into the bank. Returns the
    s2c InvToBank, or None to refuse (not owned / equipped / class item / bank full)."""
    try:
        item_id = int(item_id)
    except (TypeError, ValueError):
        return None
    cid = char["id"]
    # a class item's Quantity is class points; keep it in the bag so the class stays usable
    if _is_class_item(conn, item_id):
        return None
    already_banked = conn.execute(
        "SELECT 1 FROM char_items WHERE char_id=? AND item_id=? AND banked=1 LIMIT 1",
        (cid, item_id)).fetchone() is not None
    if not already_banked and _slots_used(conn, cid, banked=True) >= DEFAULT_BANK_SLOTS:
        return None                                     # bank full (new slot needed)
    if not _move_rows(conn, cid, item_id, to_banked=True):
        return None
    conn.commit()
    return {"Cmd": "InvToBank", "invID": item_id}


def bank_withdraw(conn, char, item_id):
    """bankToInv [itemID]: move a banked item (all its rows) back to the bag. Returns the
    s2c BankToInv, or None to refuse (not banked / bag full)."""
    try:
        item_id = int(item_id)
    except (TypeError, ValueError):
        return None
    cid = char["id"]
    already_bagged = conn.execute(
        "SELECT 1 FROM char_items WHERE char_id=? AND item_id=? AND banked=0 LIMIT 1",
        (cid, item_id)).fetchone() is not None
    if not already_bagged and _slots_used(conn, cid, banked=False) >= DEFAULT_BAG_SLOTS:
        return None                                     # bag full (new slot needed)
    if not _move_rows(conn, cid, item_id, to_banked=False):
        return None
    conn.commit()
    return {"Cmd": "BankToInv", "bankID": item_id}


def bank_swap(conn, char, inv_id, bank_id):
    """bankSwapInv [invID, bankID]: one bag item and one banked item trade places (1-for-1,
    so no slot-cap check). Returns the s2c BankSwap, or None to refuse — and moves NEITHER
    on refusal, so client and server can't diverge on a half-swap."""
    try:
        inv_id, bank_id = int(inv_id), int(bank_id)
    except (TypeError, ValueError):
        return None
    if inv_id == bank_id:       # would deposit then immediately re-withdraw the same rows
        return None
    cid = char["id"]
    if _is_class_item(conn, inv_id):
        return None
    if not _move_rows(conn, cid, inv_id, to_banked=True):
        return None
    if not _move_rows(conn, cid, bank_id, to_banked=False):
        conn.rollback()                                 # undo the deposit half
        return None
    conn.commit()
    return {"Cmd": "BankSwap", "invID": inv_id, "bankID": bank_id}


# ---- quests ----------------------------------------------------------------

def default_classes(conn):
    """ResponseDefaultClasses: the char-creation base classes (name -> armor bundle),
    served from the DB (kv 'defaultclasses', seeded from data/defaultclasses.json)
    rather than replaying a capture sample. Falls back to an empty set if unseeded."""
    blob = db.kv_get(conn, "defaultclasses")
    classes = json.loads(blob) if blob else {}
    return {"Cmd": "defaultclasses", **classes}


def change_state(char, state=1):
    """ResponseChangeState for the joining player. The client resolves the entity by
    `unm` and only acts when it matches a known player (the joiner themselves), so
    `unm` must be THIS character's name — the old static sample hardcoded drathaxie,
    making the packet a silent no-op for everyone else."""
    name = char["name"] if char is not None else ""
    return {"Cmd": "ChangeState", "unm": name, "State": state}


def load_quests(conn, ids):
    """getQuests.quests = {questID: QuestData} for the requested ids, served from the
    quests catalog (raw is the full quest def). Unknown ids are skipped.

    The client's ResponseGetQuests.quests is a Dictionary<int,QuestData> and
    Execute() reads quests.Values — so this MUST be a JSON object keyed by id, not a
    list (a JSON array won't deserialize into the dictionary and crashes the handler,
    which stalls the map join). Note getQuests is a dict here, but questData.quests
    is a List<QuestData> — different shapes per command."""
    out = {}
    for qid in ids:
        row = conn.execute("SELECT raw FROM quests WHERE quest_id=?", (qid,)).fetchone()
        if row is not None:
            q = json.loads(row["raw"])
            q["turnin"] = db.quest_turnins(conn, qid)   # generated from the normalized table
            out[str(qid)] = q
    return out


# ---- quest editor (DB manager): load/save a quest + its four normalized tables together --------

def quest_editor_data(conn, qid):
    """Everything the quest editor needs for one quest: the quest def (minus turnin), its objectives
    (quest_turnins), the per-objective drop rolls (quest_objective_drops) and kill-credit monsters
    (quest_objective_refs), and the rewards (quest_rewards). None if the quest doesn't exist."""
    row = conn.execute("SELECT raw FROM quests WHERE quest_id=?", (int(qid),)).fetchone()
    if not row:
        return None
    raw = json.loads(row["raw"])
    turnins = db.quest_turnins(conn, qid)
    qoids = [int(t["QOID"]) for t in turnins if t.get("QOID") is not None]
    drops = {}
    for q in qoids:
        r = conn.execute("SELECT chance, min_qty, max_qty FROM quest_objective_drops WHERE qoid=?",
                         (q,)).fetchone()
        if r:
            drops[str(q)] = {"chance": float(r["chance"]), "min": int(r["min_qty"]),
                             "max": int(r["max_qty"])}
    refs = {str(q): sorted(db.objective_monsters(conn, q)) for q in qoids
            if db.objective_monsters(conn, q)}
    rewards = [{"kind": r["kind"], "item_id": r["item_id"], "quantity": r["quantity"],
                "rate": r["rate"], "hidden": r["hidden"]}
               for r in conn.execute(
                   "SELECT kind, item_id, quantity, rate, hidden FROM quest_rewards WHERE quest_id=? ORDER BY idx",
                   (int(qid),))]
    quest = {k: v for k, v in raw.items() if k != "turnin"}
    return {"quest": quest, "turnins": turnins, "drops": drops, "refs": refs, "rewards": rewards}


def quest_editor_save(conn, payload):
    """Write a quest edited in the manager back to ALL its tables in one transaction: quests.raw +
    columns, quest_turnins (objectives), quest_objective_drops, quest_objective_refs, quest_rewards.
    The objectives' drops/refs are REPLACED for this quest's QOIDs (the editor is authoritative)."""
    quest = dict(payload.get("quest") or {})
    qid = int(quest.get("QuestID") or payload.get("id") or 0)
    if qid <= 0:
        return {"ok": False, "msg": "missing quest id"}
    turnins = payload.get("turnins") or []
    drops = payload.get("drops") or {}
    refs = payload.get("refs") or {}
    rewards = payload.get("rewards") or []
    quest["QuestID"] = qid
    quest["turnin"] = turnins
    # rebuild raw.Rewards (client display) from the edited rewards list, grouped by kind
    rw = {}
    for r in rewards:
        rw.setdefault(r.get("kind") or "Static", []).append(
            {"ItemID": r.get("item_id"), "Quantity": int(r.get("quantity", 1) or 1),
             "Rate": float(r.get("rate", 0) or 0), "Hidden": bool(r.get("hidden"))})
    quest["Rewards"] = rw
    conn.execute("INSERT INTO quests (quest_id, name, descr, end_text, raw) VALUES (?,?,?,?,?) "
                 "ON CONFLICT(quest_id) DO UPDATE SET name=excluded.name, descr=excluded.descr, "
                 "end_text=excluded.end_text, raw=excluded.raw",
                 (qid, quest.get("Name"), quest.get("Desc"), quest.get("EndText"),
                  json.dumps(quest, separators=(",", ":"))))
    db.store_quest_turnins(conn, qid, turnins)
    qoids = [int(t["QOID"]) for t in turnins if t.get("QOID") is not None]
    if qoids:
        ph = ",".join("?" for _ in qoids)
        conn.execute(f"DELETE FROM quest_objective_drops WHERE qoid IN ({ph})", tuple(qoids))
        conn.execute(f"DELETE FROM quest_objective_refs WHERE qoid IN ({ph})", tuple(qoids))
    for q, d in (drops or {}).items():
        conn.execute("INSERT INTO quest_objective_drops(qoid, chance, min_qty, max_qty) "
                     "VALUES(?,?,?,?)", (int(q), float(d.get("chance", 1.0) or 1.0),
                                         int(d.get("min", 1) or 1), int(d.get("max", 1) or 1)))
    for q, mons in (refs or {}).items():
        for mid in (mons or []):
            conn.execute("INSERT INTO quest_objective_refs(quest_id, qoid, mon_id) VALUES(?,?,?) "
                         "ON CONFLICT(qoid, mon_id) DO NOTHING", (qid, int(q), int(mid)))
    conn.execute("DELETE FROM quest_rewards WHERE quest_id=?", (qid,))
    for i, r in enumerate(rewards):
        conn.execute("INSERT INTO quest_rewards(quest_id, idx, kind, item_id, quantity, rate, hidden) "
                     "VALUES(?,?,?,?,?,?,?)", (qid, i, r.get("kind") or "Static",
                                               r.get("item_id"), int(r.get("quantity", 1) or 1),
                                               float(r.get("rate", 0) or 0),
                                               1 if r.get("hidden") else 0))
    conn.commit()
    return {"ok": True, "ID": qid}


# questsCopmlete is a completion BITFIELD the client indexes as questsComplete[qID >> 3]
# (PlayerQuestData.isQuestComplete). It must be pre-sized with zero bytes or any
# isQuestComplete check (NPC quest-state managers run them on spawn) throws
# IndexOutOfRange and hangs the map join. 2000 bytes covers quest ids up to 16000,
# matching the captured questData.
QUESTS_COMPLETE_BYTES = 2000


# QuestObjectiveType (client enum, QuestObjectiveType.cs): Turnin=0, Killcount=1,
# Interact=2, Talk=3, Apop=4, Cutscene=5.
QOT_TURNIN, QOT_KILL, QOT_INTERACT, QOT_TALK, QOT_APOP, QOT_CUTSCENE = range(6)


def _refresh(conn, char):
    return conn.execute("SELECT * FROM characters WHERE id=?", (char["id"],)).fetchone()


def _quest_def(conn, qid):
    row = conn.execute("SELECT raw FROM quests WHERE quest_id=?", (int(qid),)).fetchone()
    return json.loads(row["raw"]) if row else None


def _quest_turnins(conn, qid):
    """A quest's objectives — now from the normalized quest_turnins table (the source of truth),
    not parsed from raw."""
    return db.quest_turnins(conn, qid)


def _ref_ints(refids):
    """RefIDs is a comma string ('1,7,8' = monster/apop/dialog ids); return them as ints."""
    return [int(t) for t in str(refids or "").split(",") if t.strip().lstrip("-").isdigit()]


def _accepted_ids(conn, cid):
    return [r["quest_id"] for r in conn.execute(
        "SELECT quest_id FROM char_quests WHERE char_id=? AND status=1 ORDER BY quest_id", (cid,))]


def _obj_qty(conn, cid, qoid):
    r = conn.execute("SELECT quantity FROM char_quest_objectives WHERE char_id=? AND qoid=?",
                     (cid, qoid)).fetchone()
    return int(r["quantity"]) if r else 0


def _set_obj(conn, cid, qoid, qty):
    conn.execute(
        "INSERT INTO char_quest_objectives(char_id, qoid, quantity) VALUES(?,?,?) "
        "ON CONFLICT(char_id, qoid) DO UPDATE SET quantity=excluded.quantity", (cid, qoid, int(qty)))


def _quest_status(conn, cid, qid):
    r = conn.execute("SELECT status FROM char_quests WHERE char_id=? AND quest_id=?",
                     (cid, qid)).fetchone()
    return int(r["status"]) if r else 0      # 0=none, 1=accepted, 2=complete


def quest_data(conn, char):
    """ResponseQuestData built from this character's PERSISTED progress: accepted quests,
    per-objective counters, and the completion bitfield (questsCopmlete — the client's
    spelling — indexed as questsComplete[qID>>3], so it's pre-sized with zero bytes)."""
    cid = char["id"]
    accepted = _accepted_ids(conn, cid)
    completed = [r["quest_id"] for r in conn.execute(
        "SELECT quest_id FROM char_quests WHERE char_id=? AND status=2", (cid,))]
    bits = bytearray(QUESTS_COMPLETE_BYTES)
    for qid in completed:
        if 0 <= qid < QUESTS_COMPLETE_BYTES * 8:
            bits[qid >> 3] |= 1 << (qid % 8)
    objectives, quests = [], []
    for qid in accepted:
        q = _quest_def(conn, qid)
        if q is None:
            continue
        quests.append(q)
        for t in (q.get("turnin") or []):
            qoid = t.get("QOID")
            if qoid is None:
                continue
            item_id = t.get("ItemID")
            objectives.append({
                "Quantity": _obj_qty(conn, cid, qoid), "Name": t.get("Name"), "ID": qoid,
                "QuestID": qid, "ItemID": int(item_id) if item_id is not None else -1,
                "RefIds": t.get("RefIDs"), "QOType": int(t.get("QOType", 0) or 0)})
    tracked = char["tracked_quest"] if "tracked_quest" in char.keys() else 0
    return {"Cmd": "questData", "questObjectives": objectives, "questsAccepted": accepted,
            "questsCopmlete": list(bits), "TrackedID": int(tracked or 0), "quests": quests}


def accept_quest(conn, char, qid):
    """acceptQuest -> ResponseQuestAccept (Cmd 'QuestAccept'). Idempotent. A COMPLETED quest
    can be re-accepted iff it's repeatable — the client's own offer gate is
    `!IsQuestComplete || IsRepeatable` where IsRepeatable = !Once (Quest.cs:140), so the
    server honors the same flag: re-accepting flips status back to accepted and clears the
    stale objective counters (which also clears its questsComplete bit on the next questData).
    A quest with Once=true stays completed."""
    qid = int(qid)
    cid = char["id"]
    status = _quest_status(conn, cid, qid)
    if status == 2:
        q = _quest_def(conn, qid)
        if q is None or q.get("Once"):
            return {"Cmd": "QuestAccept", "QuestID": qid}   # one-time: stays completed
        conn.execute("UPDATE char_quests SET status=1 WHERE char_id=? AND quest_id=?",
                     (cid, qid))
        for t in _quest_turnins(conn, qid):                 # fresh run: zero the counters
            conn.execute("DELETE FROM char_quest_objectives WHERE char_id=? AND qoid=?",
                         (cid, t.get("QOID")))
    else:
        conn.execute("INSERT INTO char_quests(char_id, quest_id, status) VALUES(?,?,1) "
                     "ON CONFLICT(char_id, quest_id) DO NOTHING", (cid, qid))
    conn.execute("UPDATE characters SET tracked_quest=? WHERE id=?", (qid, cid))
    conn.commit()
    return {"Cmd": "QuestAccept", "QuestID": qid}


def track_quest(conn, char, qid):
    conn.execute("UPDATE characters SET tracked_quest=? WHERE id=?", (int(qid), char["id"]))
    conn.commit()


def _complete_objectives_by(conn, char, qotype, match):
    """Mark every accepted-quest objective of `qotype` whose first RefID == match as done
    (counter -> its required Quantity). Apop/Cutscene/Interact objectives complete this way."""
    cid = char["id"]
    changed = False
    for qid in _accepted_ids(conn, cid):
        for t in _quest_turnins(conn, qid):
            if int(t.get("QOType", 0) or 0) != qotype:
                continue
            refs = _ref_ints(t.get("RefIDs"))
            if refs and refs[0] == int(match):
                req = int(t.get("Quantity", 1) or 1)
                if _obj_qty(conn, cid, t["QOID"]) < req:
                    _set_obj(conn, cid, t["QOID"], req)
                    changed = True
    if changed:
        conn.commit()
    return changed


def open_apop_qo(conn, char, apopid):
    """openApopQO [apopID, monMapID]: complete an NPC Apop objective (QOType 4, RefIDs[0]=apopID)."""
    _complete_objectives_by(conn, char, QOT_APOP, apopid)
    return quest_data(conn, _refresh(conn, char))


def watch_cutscene(conn, char, csid):
    """watchCutscene [csID]: complete a Cutscene objective (QOType 5, RefIDs[0]=DialogID)."""
    _complete_objectives_by(conn, char, QOT_CUTSCENE, csid)
    return quest_data(conn, _refresh(conn, char))


def quest_objective(conn, char, qid):
    """qobjective [questID]: bump the first incomplete Killcount objective of that quest by one."""
    cid = char["id"]
    for t in _quest_turnins(conn, qid):
        if int(t.get("QOType", 0) or 0) == QOT_KILL:
            req = int(t.get("Quantity", 1) or 1)
            cur = _obj_qty(conn, cid, t["QOID"])
            if cur < req:
                _set_obj(conn, cid, t["QOID"], cur + 1)
                conn.commit()
            break
    return quest_data(conn, _refresh(conn, char))


def _saga_quest_ids(conn, sid):
    """Every quest id in a storyline (saga), from the quests' StorylineID."""
    sid = int(sid)
    out = []
    for r in conn.execute("SELECT quest_id, raw FROM quests").fetchall():
        try:
            if int(json.loads(r["raw"]).get("StorylineID", 0) or 0) == sid:
                out.append(int(r["quest_id"]))
        except Exception:
            continue
    return out


def reset_saga(conn, char, sid):
    """resetsaga [storylineID]: wipe this character's progress for every quest in that storyline
    (accepted + completed + objective counters) so the chain can be replayed. -> updateQuestBits
    (ResponseResetQuestBits rebuilds the client's completion bitfield)."""
    cid = char["id"]
    qids = _saga_quest_ids(conn, sid)
    if qids:
        ph = ",".join("?" * len(qids))
        qoids = [t["QOID"] for q in qids for t in _quest_turnins(conn, q) if t.get("QOID") is not None]
        conn.execute(f"DELETE FROM char_quests WHERE char_id=? AND quest_id IN ({ph})", (cid, *qids))
        if qoids:
            oph = ",".join("?" * len(qoids))
            conn.execute(f"DELETE FROM char_quest_objectives WHERE char_id=? AND qoid IN ({oph})",
                         (cid, *qoids))
        if char["tracked_quest"] in qids:
            conn.execute("UPDATE characters SET tracked_quest=0 WHERE id=?", (cid,))
        conn.commit()
    completed = [r["quest_id"] for r in conn.execute(
        "SELECT quest_id FROM char_quests WHERE char_id=? AND status=2", (cid,))]
    bits = bytearray(QUESTS_COMPLETE_BYTES)
    for q in completed:
        if 0 <= q < QUESTS_COMPLETE_BYTES * 8:
            bits[q >> 3] |= 1 << (q % 8)
    return {"Cmd": "updateQuestBits", "qComplete": list(bits)}


def house_save(conn, char, house_map_id, frame, data):
    """housesave [houseMapID, frame, itemsJSON]: persist the layout. -> houseSave{success}."""
    try:
        hmid = int(house_map_id)
    except (TypeError, ValueError):
        return {"Cmd": "houseSave", "success": False, "reason": "Bad house id."}
    conn.execute(
        "INSERT INTO char_houses(char_id, house_map_id, frame, data) VALUES(?,?,?,?) "
        "ON CONFLICT(char_id, house_map_id) DO UPDATE SET frame=excluded.frame, data=excluded.data",
        (char["id"], hmid, frame or "", data or "[]"))
    conn.commit()
    return {"Cmd": "houseSave", "success": True}


def machine_interact(conn, char, qid, machine_name):
    """machineInteract [questID, machineName]: credit the Interact objective (QOType 2) whose
    RefIDs matches the clicked machine. Multi-piece interacts credit +1 per piece up to the
    required count — one RefID 'DSPiece' covers the whole 'DSPiece1'..'DSPiece6' set (each click
    sends its own machine name). Re-sends questData."""
    cid = char["id"]
    if _quest_status(conn, cid, qid) == 1:
        m = str(machine_name or "")
        for t in _quest_turnins(conn, qid):
            if int(t.get("QOType", 0) or 0) != QOT_INTERACT:
                continue
            refs = [r.strip() for r in str(t.get("RefIDs") or "").split(",") if r.strip()]
            # match exactly OR by prefix, so RefIDs 'DSPiece' credits machines 'DSPiece1'..'DSPiece6'
            if not m or not refs or any(m == r or m.startswith(r) for r in refs):
                req = int(t.get("Quantity", 1) or 1)
                cur = _obj_qty(conn, cid, t["QOID"])
                if cur < req:
                    _set_obj(conn, cid, t["QOID"], min(req, cur + 1))   # one piece at a time
                    conn.commit()
    return quest_data(conn, _refresh(conn, char))


def _name_in_objective(mon_name, objective_name):
    """Whether a monster's name appears in an objective name as a WHOLE WORD (both lowercased),
    allowing a plural suffix ('Sneevil' matches 'Sneevils Defeated'). A bare substring test
    mis-credited quests — 'rat' is inside 'piRATe', so killing a Rat advanced a 'Pirate
    Defeated' objective. Word boundaries fix that while still matching multi-word names
    ('Red Dragon' in 'Red Dragon Slain'). Mirrored by questdb._kill_targets (the bot KB must
    hunt exactly what the server credits)."""
    if not mon_name or not objective_name:
        return False
    return re.search(r"(?<![a-z0-9])" + re.escape(mon_name.lower()) + r"(?:e?s)?(?![a-z0-9])",
                     objective_name.lower()) is not None


def record_kill(conn, char, mon_catalog_id, mon_name=""):
    """Server-driven killcount: on a monster kill bump Killcount objectives (QOType 1) of
    accepted quests whose RefIDs list includes this monster's catalog id. Returns True if any
    advanced. NOTE: quests whose objective carries no RefIDs (e.g. the Wyverns, QOID 159) can't
    be matched from captured data — that monster->objective mapping lives server-side in AE and
    wasn't captured; those need the mapping authored. Flagged."""
    cid = char["id"]
    try:
        mon = int(mon_catalog_id)
    except (TypeError, ValueError):
        mon = None
    mname = (mon_name or "").lower()
    if not mname and mon is not None:
        # name not resolved from the live registration — fall back to the authoritative catalog
        # name so RefID-less objectives can still be matched by name (not blanket-credited).
        row = conn.execute("SELECT name FROM monsters WHERE mon_id=?", (mon,)).fetchone()
        if row and row["name"]:
            mname = row["name"].lower()
    changed = False
    for qid in _accepted_ids(conn, cid):
        for t in _quest_turnins(conn, qid):
            if int(t.get("QOType", 0) or 0) != QOT_KILL:
                continue
            authored = db.objective_monsters(conn, t["QOID"]) if t.get("QOID") is not None else set()
            refs = _ref_ints(t.get("RefIDs"))
            amount = 1                       # kill-count objectives credit +1 per kill
            if authored:
                # AUTHORED mapping (quest_objective_refs) is authoritative: this objective is
                # credited only by the monsters explicitly listed — no name-guessing. It's also a
                # PROBABILISTIC drop: roll its chance, then a random min..max amount (so a kill can
                # drop all/some/none of a quest's objectives, in varying quantities).
                if mon is None or mon not in authored:
                    continue
                chance, lo, hi = db.objective_drop(conn, t["QOID"])
                if random.random() >= chance:
                    continue                 # didn't drop this objective this kill
                amount = random.randint(min(lo, hi), max(lo, hi))
                if amount <= 0:
                    continue
            elif refs:
                # RefIDs lists the monster catalog ids that count — match exactly.
                if mon is None or mon not in refs:
                    continue
            elif mname:
                # No RefIDs (AE resolves the target server-side; not captured). The objective
                # NAME is "<Monster> Defeated", so match it to the killed monster's name —
                # killing a Sneevil only credits a "...Sneevil..." objective, not a frogzard one.
                if not _name_in_objective(mname, t.get("Name")):
                    continue
            else:
                # No RefIDs AND the monster can't be identified -> can't match this objective.
                # Do NOT blanket-credit (that made any kill count for any quest). Skip it.
                continue
            req = int(t.get("Quantity", 1) or 1)
            cur = _obj_qty(conn, cid, t["QOID"])
            if cur < req:
                _set_obj(conn, cid, t["QOID"], min(req, cur + amount))   # cap at the requirement
                changed = True
    if changed:
        conn.commit()
    return changed


def _consume_item(conn, cid, item_id, qty):
    remaining = qty
    for r in conn.execute("SELECT char_item_id, quantity FROM char_items WHERE char_id=? AND "
                          "item_id=? AND banked=0 ORDER BY char_item_id", (cid, item_id)).fetchall():
        if remaining <= 0:
            break
        take = min(remaining, int(r["quantity"]))
        left = int(r["quantity"]) - take
        if left > 0:
            conn.execute("UPDATE char_items SET quantity=? WHERE char_item_id=?", (left, r["char_item_id"]))
        else:
            conn.execute("DELETE FROM char_items WHERE char_item_id=?", (r["char_item_id"],))
        remaining -= take


# ---- leveling ---------------------------------------------------------------
# XP required to advance FROM each level. Levels 1-5 are reachable; level 6 is gated behind a
# billion XP (AE's alpha cap — captured players sit at ExpToLevel 999999999). At/above the cap
# the level is frozen and the bar can't fill. exp is per-level progress (overflow carries on a
# level-up), matching the client's Exp/ExpToLevel XP bar.
LEVEL_XP = {1: 100, 2: 300, 3: 600, 4: 1000, 5: 1_000_000_000}
MAX_LEVEL = 6
_LEVEL_GATE = 999_999_999


def xp_to_level(level):
    """XP needed to go from `level` to the next. Frozen at the gate for level >= MAX_LEVEL."""
    return LEVEL_XP.get(int(level or 1), _LEVEL_GATE)


def grant_xp(conn, char, amount):
    """Add XP and process level-ups per the curve (overflow carries forward). Persists level+exp.
    Returns (level, exp, leveled) — leveled True if the level increased (caller sends LevelUp)."""
    level = int(char["level"] or 1)
    exp = int(char["exp"] or 0) + max(0, int(amount or 0))
    leveled = False
    while level < MAX_LEVEL and exp >= xp_to_level(level):
        exp -= xp_to_level(level)
        level += 1
        leveled = True
    if level >= MAX_LEVEL:
        exp = min(exp, _LEVEL_GATE)
    conn.execute("UPDATE characters SET level=?, exp=? WHERE id=?", (level, exp, char["id"]))
    return level, exp, leveled


def levelup_packet(char, level, exp, maxhp):
    """ResponseLevelUp (Cmd 'LevelUp'): the client applies Level/HPMax/Exp/ExpToLevel. unm must be
    lowercase — ResponseLevelUp.Execute does Entity.getPlayer(unm), and players are registered under
    their lowercase name (the chat-bubble fix); mixed case -> null -> the level never applies."""
    return {"Cmd": "LevelUp", "unm": (char["name"] or "").lower(), "Level": int(level),
            "HPMax": int(maxhp), "ExpToLevel": xp_to_level(level), "Exp": int(exp)}


def _grant_quest_rewards(conn, char, q):
    """Grant the quest's gold/exp/items. Returns the granted item wire dicts (with CharItemID) so
    the caller can push them as a live addItems — otherwise the reward only shows after a relog."""
    cid = char["id"]
    gold = int(q.get("Gold", 0) or 0)
    exp = int(q.get("Exp", 0) or 0)
    if gold:
        conn.execute("UPDATE characters SET gold=gold+? WHERE id=?", (gold, cid))
    if exp:
        grant_xp(conn, char, exp)           # quest XP levels you up too (per the curve)
    granted = []
    for r in conn.execute("SELECT item_id FROM quest_rewards WHERE quest_id=?",
                          (int(q.get("QuestID", 0) or 0),)):
        item = db.item(conn, r["item_id"])
        if item:
            ci = _grant_item(conn, cid, item)
            wire = dict(item)
            wire["CharItemID"] = ci
            wire["Quantity"] = int(wire.get("Quantity", 1) or 1)
            granted.append(wire)
    return granted


def auto_turnin(conn, char):
    """Complete any accepted quest flagged AutoTurnIn whose objectives are now all met. These
    are the walk-to / talk-to / watch-cutscene steps (TurnInType 2) that turn in automatically;
    kill quests are AutoTurnIn=false and wait for a manual turn-in at the NPC. Returns the QComp
    results for the quests that completed."""
    out = []
    for qid in _accepted_ids(conn, char["id"]):
        q = _quest_def(conn, qid)
        if not (q and q.get("AutoTurnIn")):
            continue
        r = try_quest_complete(conn, char, qid)
        if r.get("Success"):
            out.append(r)
    return out


def try_quest_complete(conn, char, qid, choice=-1):
    """tryQuestComplete [questID(,choice)]: if every objective is met (Apop/Cutscene/Kill
    counters at required, and item turn-ins present in the bag), complete the quest — set the
    bit, consume turn-in items, clear counters, grant rewards. -> ResponseQuestComplete (QComp)."""
    cid = char["id"]
    qid = int(qid)
    q = _quest_def(conn, qid)
    ntype = int((q or {}).get("NotificationType", 1) or 1)
    fail = {"Cmd": "QComp", "ID": qid, "Success": False, "Title": "",
            "Message": "You do not meet the requirements to complete this Quest.",
            "IndexType": 0, "NotificationType": ntype}
    if q is None:
        return fail
    turnins = _quest_turnins(conn, qid)
    status = _quest_status(conn, cid, qid)
    if status == 2:                                           # already complete
        return fail
    # An objective-less quest (e.g. "Watch Alpha Cutscene", QuestID 22 — no turn-ins) has no
    # requirements to check, so it completes on request even without a prior acceptQuest (the
    # client fires tryQuestComplete for these directly). A quest WITH objectives still must be
    # accepted before it can be turned in.
    if status != 1 and turnins:
        return fail
    consume = []
    for t in turnins:
        qot = int(t.get("QOType", 0) or 0)
        req = int(t.get("Quantity", 1) or 1)
        if qot == QOT_TURNIN:                                  # turn-in: the item id is on ItemID
            item_id = int(t.get("ItemID", -1) or -1)           # or, for itemTurnin, in RefIDs
            if item_id <= 0:
                refs = _ref_ints(t.get("RefIDs"))
                item_id = refs[0] if refs else -1
            if item_id > 0:                                    # need the item(s) in the bag
                have = conn.execute("SELECT COALESCE(SUM(quantity),0) AS n FROM char_items WHERE "
                                    "char_id=? AND item_id=? AND banked=0", (cid, item_id)).fetchone()["n"]
                if int(have) < req:
                    return fail
                consume.append((item_id, req))
                continue
        if _obj_qty(conn, cid, t["QOID"]) < req:               # counter objective (kill/apop/cutscene)
            return fail
    for item_id, req in consume:
        _consume_item(conn, cid, item_id, req)
    conn.execute("INSERT INTO char_quests(char_id, quest_id, status) VALUES(?,?,2) "
                 "ON CONFLICT(char_id, quest_id) DO UPDATE SET status=2", (cid, qid))
    for t in turnins:
        conn.execute("DELETE FROM char_quest_objectives WHERE char_id=? AND qoid=?", (cid, t.get("QOID")))
    rewards = _grant_quest_rewards(conn, char, q)
    conn.commit()
    return {"Cmd": "QComp", "ID": qid, "Success": True, "Title": q.get("Name") or "",
            "Message": "", "IndexType": 0, "NotificationType": ntype,
            "rewardItems": rewards}              # popped + pushed as addItems by the handler


def abandon_quest(conn, char, qid):
    """qabandon [questID]: drop an accepted quest — remove it from char_quests and clear its
    objective progress. Returns the refreshed questData panel."""
    cid = char["id"]
    conn.execute("DELETE FROM char_quests WHERE char_id=? AND quest_id=?", (cid, int(qid)))
    for t in _quest_turnins(conn, int(qid)):
        conn.execute("DELETE FROM char_quest_objectives WHERE char_id=? AND qoid=?",
                     (cid, t.get("QOID")))
    conn.commit()
    return quest_data(conn, char)


# EquipSpots enum -> the user.eqp key the avatar rig uses for that slot. The Class spot
# (6) is intentionally absent: the class's full rig (skin + ClassParticleBundle) is owned
# by the classes table and applied authoritatively in build_init_player, not rebuilt from
# the stripped catalog item.
_EQP_SPOT_NAMES = {2: "Weapon", 3: "Head", 4: "Back", 5: "Pet",
                   7: "Armor", 10: "Amulet", 11: "GuildItem"}


def _equipped_rig(conn, char_id):
    """{spotName: eqpEntry} for every equipped item, built from the item catalog —
    this is how equipped gear renders on the avatar (user.eqp)."""
    rig = {}
    for ci in conn.execute(
            "SELECT item_id FROM char_items WHERE char_id=? AND equipped=1 AND banked=0",
            (char_id,)):
        item = db.item(conn, ci["item_id"])
        if item is None:
            continue
        spot = int(item.get("EquipSpot", 0) or 0)
        name = _EQP_SPOT_NAMES.get(spot)
        if not name:
            continue
        entry = {"ID": item.get("ID", ci["item_id"]), "Bundle": item.get("Bundle"),
                 "PrefabName": item.get("PrefabName"), "EquipSpot": spot,
                 "ItemType": item.get("ItemType")}
        for k in ("ClassParticleBundle", "Scale", "OffsetX", "OffsetY"):
            if item.get(k) is not None:
                entry[k] = item[k]
        rig[name] = entry
    return rig


# ---- initPlayer ------------------------------------------------------------

def uid_for(char):
    """Stable, unique per-character network id (so clients tell each other apart)."""
    return 1_000_000 + int(char["id"])


def _hair_info(conn, hair_id, gender=None):
    """Resolve a HairInfo {ID,Name,Filename,Gender,Bundle} for a hair_id from the hairs catalog
    (the same list /charedit picks from). `gender` is accepted for call-site compatibility (a
    hair_id is unique in the table, so it no longer disambiguates duplicates)."""
    return db.hair(conn, hair_id)


def load_hairshop(conn, shop_id):
    """ResponseLoadHairShop: the hair catalog as HairData {HairID,sName,sFile,sGen,bundle}, from
    the hairs table (the same catalog /charedit uses). This is the PUBLIC path to character
    customization (the HairShop apop button) — any player, no staff gate — so the client opens the
    customization overlay seeded with these hairs."""
    hairs = db.hairs_list(conn)
    hair = [{"HairID": h.get("ID"), "sName": h.get("Name"), "sFile": h.get("Filename"),
             "sGen": h.get("Gender"), "bundle": h.get("Bundle")} for h in hairs]
    return {"Cmd": "loadHairShop", "HairShopID": shop_id, "hair": hair}


def build_init_player(conn, char):
    """initPlayer built FRESH from this character's DB state — identity, currencies, level, the
    full stat block, customization, the equipped rig, inventory and the class skill bar — with NO
    captured template underneath. Unmodelled subsystems (loose gems, houses, friends) return honest
    empty arrays; the client-display constants (rage bar, slot caps) are account-agnostic."""
    uid = uid_for(char)
    access = char["access_level"] if "access_level" in char.keys() else 0

    # userPrefs (UI toggles) — all-on defaults, overridden by the per-char `prefs` column.
    prefs = dict(_DEFAULT_USER_PREFS)
    if "prefs" in char.keys() and char["prefs"]:
        try:
            prefs.update(json.loads(char["prefs"]) or {})
        except (TypeError, ValueError):
            pass

    # The equipped class drives the visual rig (skin + ClassParticleBundle) AND the skill bar.
    # A real ClassID is REQUIRED for the client to activate the class; a fresh character has the
    # starter Warrior, so fall back to it only for a class-less edge case.
    eff_class = int(char["class_id"] or 0) or STARTER_CLASS_ID
    sclass, class_rig = "", None
    crow = conn.execute("SELECT name, rig FROM classes WHERE class_id=?", (eff_class,)).fetchone()
    if crow:
        sclass = crow["name"] or ""
        if crow["rig"]:
            try:
                class_rig = json.loads(crow["rig"])
            except (TypeError, ValueError):
                class_rig = None
    seact = forge.build_seact(conn, eff_class)        # {Cmd:sEAct, skillList, particleList}

    # Equipped rig: the class skin (anti-naked fallback + the ClassParticleBundle skills/auras
    # need) plus ONLY the spots this character actually has equipped; unequipped spots fall back
    # to the skin/hair on the client.
    eqp = {}
    if class_rig:
        eqp["Class"] = class_rig
    for spot_name, entry in _equipped_rig(conn, char["id"]).items():
        eqp[spot_name] = entry

    # Customization: colours from columns + the CHOSEN hair's bundle. The client renders hair from
    # HairBundle/HairName (HairID alone does nothing), so resolve the hair catalog entry.
    cust = {jk: char[col] for jk, col in _COLOR_COLS.items()}
    cust["HairID"] = char["hair_id"]
    cust["HairPrefab"] = "ArmorSlots"
    _hi = _hair_info(conn, char["hair_id"], char["gender"])
    if _hi is not None:
        cust["HairName"] = _hi.get("Name")
        cust["HairBundle"] = _hi.get("Bundle")

    sta, maxhp = full_sta(char, pattern_bonus(conn, char["id"]))

    user = {
        # In-world Name MUST be lowercase: it's the playersByName key and the overhead chat-bubble
        # does getPlayer(name.ToLower()); a mixed-case Name never matches. [[chat-email-guard-gotcha]]
        "Name": char["name"].lower(),
        "customization": cust,
        "eqp": eqp,
        "sClass": sclass,
        "stats": sta,
        "uid": uid,
        "strGender": char["gender"],
        "intLevel": char["level"],
        "intAccessLevel": access,                     # >=50 unlocks the dev panel
        "iUpgDays": 0,                                # membership: UpgradeDays>0 == member; 0 = free
        "intState": 1,                                # alive
        "intRPMax": USER_RP_MAX,
        "intRPColor": USER_RP_COLOR,
        "intThreshold": USER_THRESHOLD,
        "intThresholdColor": USER_THRESHOLD_COLOR,
        "intHP": maxhp,
        "intHPMax": maxhp,
        "showHelm": bool(prefs.get("ShowHelm", True)),
        "showCloak": bool(prefs.get("ShowCloak", True)),
        "strFrame": "Enter",
        "strPad": "Spawn",
        "particleList": seact.get("particleList", []),
    }

    # playerInfo: THIS character's identity + neutral free-account defaults; never another
    # account's membership/slots/guild/achievements.
    created_iso = DEFAULT_CREATED
    if "account_id" in char.keys():
        arow = conn.execute("SELECT created FROM accounts WHERE id=?",
                            (char["account_id"],)).fetchone()
        if arow and arow["created"]:
            try:
                created_iso = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(float(arow["created"])))
            except (TypeError, ValueError):
                pass
    try:
        achievements = json.loads(char["achievements"] or "{}")   # per-char bitfields; ip25 gates
    except (ValueError, TypeError):                                # Despair's founder shops. NOT null.
        achievements = {}

    pinfo = {
        "CharID": char["id"],
        "ClassID": eff_class,
        "intAccessLevel": access,
        "Gold": char["gold"],
        "Coins": char["coins"],
        "Exp": char["exp"],
        "ExpToLevel": xp_to_level(char["level"]),     # XP bar for the current level
        # MUST be non-empty: the client's chat email-guard does s.Contains(Info.Email), and in C#
        # "".Contains("") is always true -> every chat line is silently blocked. A per-char
        # placeholder keeps it private AND un-typeable in normal chat. [[chat-email-guard-gotcha]]
        "Email": f"player{char['id']}@infinityserver.local",
        "dateCreated": created_iso,                   # the account's real creation time (or default)
        "upgradeExpires": DEFAULT_UPGRADE_EXPIRES,    # DateTime; membership is gated on UpgradeDays
        "UpgradeDays": 0,
        "iUpg": 0,                                    # membership flag — free account
        "ActivationFlag": DEFAULT_ACTIVATION_FLAG,
        "Age": 0,
        "Buyer": False,
        "intHits": 0,
        "MQ": 0, "DF": 0, "AQ": 0,                    # other-game character ids
        "BankSlots": DEFAULT_BANK_SLOTS,
        "BagSlots": DEFAULT_BAG_SLOTS,
        "HouseSlots": DEFAULT_HOUSE_SLOTS,
        "EquippedHouseItemID": -1,                    # the client's no-house default
        "achievements": achievements,
        "guild": None,                                # null is the client's real "no guild" value
    }

    return {
        "Cmd": "initPlayer",
        "user": user,
        "userPrefs": prefs,
        "playerInfo": pinfo,
        "items": inventory(conn, char["id"]),
        "loot": [],                                   # pending loot is per-uid (loot.py); none at login
        "patterns": patterns.loose_gems(conn, char["id"]),   # the enhancement gem bag (equipPattern)
        "houseItems": [],                             # houses — not modelled
        "friends": [],                                # social — not modelled
        "Actions": seact,                             # the class skill bar (sEAct) the HUD shows
    }


def charselect_entry(conn, char):
    """A CharSelect `account.chars[0]` entry built FRESH from this character — identity, currencies,
    colours, the chosen hair, class skin, the equipped rig and HP bar — with NO captured template.
    The preview-shell fields the screen doesn't drive from gameplay (pvpTeam, RP bar, spawn pad,
    monTransform) are neutral zero/null defaults, matching AE's CharSelect packet."""
    access = char["access_level"] if "access_level" in char.keys() else 0

    # colours from columns + the chosen hair's bundle (HairID alone renders nothing)
    cust = {jk: char[col] for jk, col in _COLOR_COLS.items()}
    cust["HairID"] = char["hair_id"]
    cust["HairPrefab"] = "ArmorSlots"
    _hi = _hair_info(conn, char["hair_id"], char["gender"])
    if _hi is not None:
        cust["HairName"] = _hi.get("Name")
        cust["HairBundle"] = _hi.get("Bundle")

    # class skin + the character's actually-equipped spots (no template gear)
    eff_class = int(char["class_id"] or 0) or STARTER_CLASS_ID
    sclass, class_rig = "", None
    crow = conn.execute("SELECT name, rig FROM classes WHERE class_id=?", (eff_class,)).fetchone()
    if crow:
        sclass = crow["name"] or ""
        if crow["rig"]:
            try:
                class_rig = json.loads(crow["rig"])
            except (TypeError, ValueError):
                class_rig = None
    eqp = {}
    if class_rig:
        eqp["Class"] = class_rig
    for spot_name, e in _equipped_rig(conn, char["id"]).items():
        eqp[spot_name] = e

    sta, maxhp = full_sta(char, pattern_bonus(conn, char["id"]))
    return {
        "charid": char["id"],
        "Name": char["name"],
        "customization": cust,
        "eqp": eqp,
        "sClass": sclass,
        "stats": sta,
        "uid": uid_for(char),
        "strGender": char["gender"],
        "intLevel": char["level"],
        "mobileLevel": char["level"],
        "mobileExp": char["exp"],
        "mobileGold": char["gold"],
        "intAccessLevel": access,
        "intHP": maxhp,
        "intHPMax": maxhp,
        # --- preview-shell defaults (AE's CharSelect packet sends these as 0/null) ---
        "pvpTeam": 0,
        "iUpgDays": 0,
        "isFounder": False,
        "intState": 0,
        "intRP": 0,
        "intRPMax": 0,
        "intRPColor": 0,
        "intThreshold": 0,
        "intThresholdColor": 0,
        "showHelm": True,
        "showCloak": True,
        "strFrame": None,
        "strPad": None,
        "x": 0.0,
        "y": 0.0,
        "particleList": None,
        "monTransformBundle": None,
        "monTransformLinkage": None,
        "monTransformScale": None,
    }


def build_account(conn, char, username, token):
    """The launcher loginData `account` block, built FRESH from this account/character — identity,
    currencies and the CharSelect entry. Never the captured account's email/userid/currencies."""
    access = char["access_level"] if "access_level" in char.keys() else 0
    created_iso = DEFAULT_CREATED
    if "account_id" in char.keys():
        arow = conn.execute("SELECT created FROM accounts WHERE id=?",
                            (char["account_id"],)).fetchone()
        if arow and arow["created"]:
            try:
                created_iso = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(float(arow["created"])))
            except (TypeError, ValueError):
                pass
    return {
        "userid": uid_for(char),
        "iAccess": access,
        "iUpg": 0,
        "iAge": 0,
        "iLevel": char["level"],
        "mobileLevel": char["level"],
        "mobileGold": char["gold"],
        "mobileExp": char["exp"],
        "iUpgDays": 0,
        "bCCOnly": 0,
        "intHours": 0,
        "iEmailStatus": 0,
        "iGold": char["gold"],
        "iCoins": char["coins"],
        "sToken": token,
        # per-account placeholder — never a real email (chat email-guard + privacy)
        "strEmail": f"player{char['id']}@infinityserver.local",
        "strCountryCode": "US",
        "unm": username,                          # the typed name the game-server Login echoes
        "dUpgExp": DEFAULT_UPGRADE_EXPIRES,
        "dCreated": created_iso,
        "hasAlphaAccess": True,
        "chars": [charselect_entry(conn, char)],
    }


def build_login_response(char):
    """loginResponse built fresh: this account's unique UserID + name, plus our server-wide
    news/version config and MOTD (identical for every player — server config, not account data)."""
    return {
        "Cmd": "loginResponse",
        "Success": True,
        "UserID": uid_for(char),
        "Username": char["name"],
        "serverTime": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime()),
        "info": SERVER_NEWS_INFO,
        "MOTD": SERVER_MOTD,
        "TestResponse": "",
        "failMessage": "",
    }


def pattern_bonus(conn, char_id):
    """The keystone of the 1=1 stat model: aggregate every EQUIPPED gem (pattern) into the
    contribution it makes to a character's combat stats. Gems are the single source of the six
    primary stats, the weapon's damage range, and flat (helm) HP. Applying/removing a gem
    recomputes the `sta` (capture: UpdatePattern.stats). Returns
    {STR,END,DEX,INT,WIS,LCK, hp, weapon:(min,max)|None}."""
    bonus = {"STR": 0, "END": 0, "DEX": 0, "INT": 0, "WIS": 0, "LCK": 0, "hp": 0, "weapon": None}
    for ci in conn.execute(
            "SELECT ci.*, it.equip_spot AS item_equip_spot FROM char_items ci "
            "JOIN items it ON it.item_id=ci.item_id "
            "WHERE ci.char_id=? AND ci.equipped=1 AND ci.banked=0", (char_id,)):
        pat = patterns.applied(ci)
        if not pat:
            continue
        for k, v in patterns.primary_stats(pat).items():
            bonus[k] += v
        bonus["hp"] += patterns.flat_hp(pat)
        spot = int(ci["item_equip_spot"] or 0)   # the equipped item's slot decides the weapon gem
        if spot == patterns.WEAPON:
            wr = patterns.weapon_range(pat)
            if wr:
                bonus["weapon"] = wr
    return bonus


def build_combat_stats(char, bonus=None):
    """Derive the player's combat stat block (the statUpdate `sta`) + MaxHP from their BASE
    attributes + level, PLUS the equipped-gem `bonus` (pattern_bonus) — gems are the source of
    stats (keystone). AE computes the secondary stats server-side; tcr/scm are refit EXACT to the
    capture, the rest are OURS — but damage is now gem-, stat- and level-driven instead of flat."""
    def g(col):
        try:
            return int(char[col] or 0)
        except (KeyError, IndexError, TypeError):
            return 0
    b = bonus or {}
    STR = g("stat_str") + int(b.get("STR", 0) or 0)
    END = g("stat_end") + int(b.get("END", 0) or 0)
    DEX = g("stat_dex") + int(b.get("DEX", 0) or 0)
    INT = g("stat_int") + int(b.get("INT", 0) or 0)
    WIS = g("stat_wis") + int(b.get("WIS", 0) or 0)
    LCK = g("stat_lck") + int(b.get("LCK", 0) or 0)
    lvl = g("level") or 1
    ap = round(10 + STR * 1.0 + DEX * 0.3 + lvl * 2)        # physical attack power
    sp = round(10 + INT * 1.0 + WIS * 0.3 + lvl * 2)        # spell power
    # Refit to the captured `sta` (P1-2). These coefficients are OURS (AE's are server-internal)
    # but now PRODUCE the captured values for the captured stat line instead of inventing them:
    #   tcr 0.0114, scm 1.538, MaxHP 1337 at STR14/END18/DEX12/INT11/WIS10/LCK13 (HP at lvl 1).
    tcr = round(min(0.5, LCK * 0.0005 + DEX * 0.0004), 4)   # crit chance (was ~10x too high)
    scm = round(1.5 + LCK * 0.003, 3)                       # crit damage multiplier (was high)
    # to-hit accuracy (capture `tha` ~0.99): combat rolls a Miss with prob (1 - tha). DEX
    # raises accuracy, capped so a miss is always possible.
    tha = round(min(0.998, 0.97 + DEX * 0.0015), 4)
    # MaxHP = base(END, lvl) + the flat HP from equipped (helm/health) gems (keystone:
    # capture shows MaxHP is NOT END-linear — the gem HP is the missing term).
    maxhp = round(300 + END * 56 + lvl * 20) + int(b.get("hp", 0) or 0)
    sta = {"ap": ap, "sp": sp, "tcr": tcr, "scm": scm, "tha": tha,
           "STR": STR, "END": END, "DEX": DEX, "INT": INT, "WIS": WIS, "LCK": LCK}
    return sta, maxhp


def full_sta(char, bonus=None):
    """The complete wire `sta` block: the computed primaries/derived (build_combat_stats) on top of
    the damage-type in/out multiplier defaults the client expects (all 1.0 for a base character with
    no resistance modifiers). Returns (sta, maxhp). Replaces merging over a captured statUpdate."""
    sta, maxhp = build_combat_stats(char, bonus)
    return {**_STA_MULTIPLIERS, **sta}, maxhp


def combat_sta(char, bonus=None):
    """The full wire `sta` block (multipliers + computed stats). Used by statUpdate and by
    UpdatePattern.stats (the recomputed stats a gem change carries — applying a gem returns the
    full new `sta`)."""
    sta, _ = full_sta(char, bonus)
    return sta


def build_stat_update(char, hp=None, bonus=None):
    """statUpdate carrying the persisted gold, this player's network id, and their
    stat-derived combat stats + HP. `bonus` = the equipped-gem contribution (pattern_bonus);
    when given, the six primary stats, MaxHP and the weapon range all fold in the gems.
    `hp` = the player's CURRENT HP (clamped to MaxHP); when None it defaults to full, which is
    correct for login / a stat refresh but NOT after a kill — passing the live HP there stops
    the post-kill statUpdate from silently healing the player to full (P0-3).
    `ResponseStatUpdate.Execute` sets `player.HP = HP`, so a full-HP statUpdate is a heal."""
    sta, maxhp = full_sta(char, bonus)
    su = {
        "Cmd": "statUpdate",
        "player": f"p:{uid_for(char)}",
        "sta": sta,                            # full block: multipliers + computed stats
        "MaxHP": maxhp,
        "HP": maxhp if hp is None else max(0, min(int(hp), maxhp)),
    }
    # weapon-damage range (the statUpdate DmgMin/DmgMax the HUD shows). With an equipped weapon
    # gem this IS the gem's Base*(1-+Wild) range (keystone, CONFIRMED vs the 27-34 tooltip);
    # without one it falls back to OUR attack-power-derived range (P1-2).
    weapon = (bonus or {}).get("weapon")
    if weapon:
        su["DmgMin"], su["DmgMax"] = int(weapon[0]), int(weapon[1])
    else:
        su["DmgMin"] = round(sta["ap"] * combat.WEAPON_MIN)
        su["DmgMax"] = round(sta["ap"] * combat.WEAPON_MAX)
    return su


# ---- shops -----------------------------------------------------------------

def shop_listing(conn, shop_item):
    """Rebuild one shop entry: the shared catalog item def re-joined with this
    shop's instance fields (ShopItemID, QuantityRemain)."""
    item = db.item(conn, shop_item["item_id"]) or {"ID": shop_item["item_id"]}
    item["ShopItemID"] = shop_item["shop_item_id"]
    item["QuantityRemain"] = shop_item["quantity_remain"]
    return item


def load_shop(conn, shop_id):
    """Assemble a loadShop response from the normalized catalog (or None)."""
    blob = db.shop_blob(conn, shop_id)          # loadShop wrapper generated from columns
    if blob is None:
        return None
    blob["shop"]["items"] = [
        shop_listing(conn, si) for si in conn.execute(
            "SELECT * FROM shop_items WHERE shop_id=? ORDER BY shop_item_id", (shop_id,))
    ]
    return blob


# ---- economy ---------------------------------------------------------------

def buy(conn, char, params):
    """params = [charID, shopID, shopItemID] -> ResponseBuyItem dict."""
    try:
        shop_id, shop_item_id = int(params[1]), int(params[2])
    except (IndexError, ValueError):
        return {"Cmd": "buyItem", "Success": False, "Message": "Bad request."}

    row = conn.execute(
        "SELECT * FROM shop_items WHERE shop_id=? AND shop_item_id=?",
        (shop_id, shop_item_id),
    ).fetchone()
    if row is None:
        return {"Cmd": "buyItem", "Success": False, "Message": "Item not available."}

    cost = int(row["cost"])
    use_coins = bool(row["coins"])
    balance = char["coins"] if use_coins else char["gold"]
    if balance < cost:
        return {"Cmd": "buyItem", "Success": False,
                "Message": "Not enough " + ("coins." if use_coins else "gold.")}

    # Pull the item definition from the shared catalog (already free of the
    # shop-instance fields) and turn it into an owned InventoryItem.
    item = db.item(conn, row["item_id"])
    if item is None:
        return {"Cmd": "buyItem", "Success": False, "Message": "Item not available."}
    field = "coins" if use_coins else "gold"
    conn.execute(f"UPDATE characters SET {field}={field}-? WHERE id=?", (cost, char["id"]))
    cid = _grant_item(conn, char["id"], item)
    conn.commit()
    item["CharItemID"] = cid
    item["LootID"] = -1
    item["Quantity"] = 1
    return {"Cmd": "buyItem", "Success": True, "Show": True, "IsDrop": False,
            "Cost": cost, "item": item}


def sell(conn, char, params):
    """params = [itemID, qty] -> ResponseSellItem dict.

    The client sends the catalog item ID (RequestSellItem(playerItem.ID)), not
    the CharItemID, so we resolve to one owned instance of that item.
    """
    try:
        item_id = int(params[0])
        qty = int(params[1]) if len(params) > 1 else 1
    except (IndexError, ValueError):
        return {"Cmd": "sellItem", "Success": False, "Message": "Bad request."}

    # Class items are NOT a sellable stack — their Quantity is class points (rank). Selling
    # one decremented CP (the live 302499 corruption); reject it outright (P2-1).
    if _is_class_item(conn, item_id):
        return {"Cmd": "sellItem", "Success": False, "Message": "Class items can't be sold."}

    # Never sell an EQUIPPED piece: its row is still worn on the avatar/HUD, and selling it would
    # delete the row (and clobber any per-instance gem roll) while the client shows it equipped.
    # Prefer a plain (un-gemmed) copy so a stack sale can't destroy a gem roll when a plain one
    # exists. (pattern_json IS NOT NULL sorts False<True -> un-gemmed first.)
    row = conn.execute(
        "SELECT * FROM char_items WHERE item_id=? AND char_id=? AND banked=0 AND equipped=0 "
        "ORDER BY (pattern_json IS NOT NULL), char_item_id LIMIT 1", (item_id, char["id"]),
    ).fetchone()
    if row is None:
        equipped_only = conn.execute(
            "SELECT 1 FROM char_items WHERE item_id=? AND char_id=? AND banked=0 AND equipped=1 "
            "LIMIT 1", (item_id, char["id"])).fetchone()
        return {"Cmd": "sellItem", "Success": False,
                "Message": ("Unequip that item before selling it." if equipped_only
                            else "You don't own that.")}
    char_item_id = int(row["char_item_id"])

    item = db.item(conn, item_id) or {}
    unit = int(item.get("Cost", 0) or 0)
    sell_price = max(1, unit // 4)          # typical AQW resale fraction
    use_coins = bool(item.get("Coins"))
    qty = max(1, min(qty, int(row["quantity"])))
    amount = sell_price * qty

    remaining = int(row["quantity"]) - qty
    if remaining > 0:
        conn.execute("UPDATE char_items SET quantity=? WHERE char_item_id=?",
                     (remaining, char_item_id))
    else:
        conn.execute("DELETE FROM char_items WHERE char_item_id=?", (char_item_id,))

    field = "coins" if use_coins else "gold"
    conn.execute(f"UPDATE characters SET {field}={field}+? WHERE id=?", (amount, char["id"]))
    conn.commit()
    return {"Cmd": "sellItem", "Success": True, "Coins": use_coins,
            "Amount": amount, "CharItemID": char_item_id}


# Valid `lockedMode` values (the client's RequirementLockType enum). The client deserializes the
# WHOLE getApop batch at once, so ONE apop carrying an unknown value (e.g. "Show") throws in
# ResponseGetApop and bricks EVERY NPC in the area — this is what made BattleOn un-joinable.
_VALID_LOCK_MODES = {"Hide", "Gray", "Lock"}
_LOCKMODE_RE = re.compile(r'"lockedMode"\s*:\s*"([^"]*)"')


def sanitize_apop_raw(raw):
    """Coerce any invalid apop `lockedMode` to "Hide" so a single bad enum value can't crash the
    client's batch apop parse. Returns `raw` untouched when every value is already valid (the
    common case), so it's cheap on the getApop hot path."""
    if not raw or '"lockedMode"' not in raw:
        return raw
    if all(m.group(1) in _VALID_LOCK_MODES for m in _LOCKMODE_RE.finditer(raw)):
        return raw
    return _LOCKMODE_RE.sub(
        lambda m: m.group(0) if m.group(1) in _VALID_LOCK_MODES else '"lockedMode":"Hide"', raw)


def load_apops(conn, ids):
    """getApop.apopData = {apopID: apopJSONString} for the requested ids (the client json.loads
    each string itself). Served from the apops table — the authoritative, live-editable catalog
    (seeded from data/apops.json; CreateNewApop / DialoggerSave mutate it in place, AE-style).
    Each raw is run through sanitize_apop_raw so a stale bad `lockedMode` can't brick the batch."""
    out = {}
    for aid in ids:
        row = conn.execute("SELECT raw FROM apops WHERE apop_id=?", (aid,)).fetchone()
        out[str(aid)] = sanitize_apop_raw(row["raw"]) if row else _empty_apop(aid)
    return out


def _empty_apop(aid):
    """A valid-but-empty apop for an id we don't have yet. The client's NPCButton.LoadButton
    calls apopData.GetAllQuests() BEFORE its `apopData == null` check, so a missing apop (null)
    throws an NRE and the NPC renders as a non-interactive 'ghost'. Returning a parseable empty
    apop keeps GetApopData non-null -> IsAvailable is false -> the button just hides, no crash."""
    return json.dumps({"ID": int(aid), "name": "", "nextElementId": 0, "startingPanels": [],
                       "panels": [], "actors": [], "freezeClient": False, "IsSingleAction": False},
                      separators=(",", ":"))


# A valid cutscene that just fades and completes (~0.6s) — served for cutscenes we don't have so
# the client's player never HANGS waiting for an empty scene to finish (a soft-lock). The
# quest/storyline step that triggered it then advances instead.
_MINIMAL_CUTSCENE = json.dumps({
    "ID": "", "cutsceneName": "", "cutsceneDescription": "", "idCount": 0, "boxCount": 0,
    "trackCount": 0, "sfxCount": 0, "completeActions": [],
    "frames": [["FadeToBlack", "Timer{0.3}"], ["FadeFromBlack", "Timer{0.3}"]]},
    separators=(",", ":"))


def load_dialog(conn, dialog_id):
    """getDialog: the saved cutscene JSON for an id — from the cutscenes store the Dialogger
    editor writes via DialoggerSave. A missing/empty one returns a minimal fade-and-complete
    cutscene (never ""), so the client doesn't hang on an empty scene. (ResponseGetDialog reads
    data.JsonText and HtmlDecodes it, so the stored &lt;/&gt; escaping round-trips.)"""
    try:
        row = conn.execute("SELECT raw FROM cutscenes WHERE id=?", (int(dialog_id),)).fetchone()
    except (TypeError, ValueError):
        return _MINIMAL_CUTSCENE
    return row["raw"] if (row and row["raw"]) else _MINIMAL_CUTSCENE


def give_item(conn, char, item_id, qty=1):
    """Grant `qty` of catalog item `item_id` to a character (dev /item cheat).
    Returns the item dict shaped for an ResponseAddOrUpdateItems packet, or None
    if the item isn't in our catalog. Stacks onto an existing row like a normal
    grant; class items are granted once at maxed CP (their Quantity is class points)."""
    try:
        item_id = int(item_id)
        qty = max(1, int(qty))
    except (TypeError, ValueError):
        return None
    item = db.item(conn, item_id)
    if item is None:
        return None
    item["Quantity"] = qty
    cid = _grant_item(conn, char["id"], item)
    conn.commit()
    # Re-read the persisted stack count so the client shows the true total after a merge.
    row = conn.execute("SELECT quantity FROM char_items WHERE char_item_id=?", (cid,)).fetchone()
    item["CharItemID"] = cid
    item["LootID"] = -1
    item["Quantity"] = int(row["quantity"]) if row else qty
    return item


def remove_item(conn, char, params):
    """removeItem [itemID, qty]: permanently delete owned copies. The client
    removes from its own UI optimistically; we persist so it stays gone on relog."""
    try:
        item_id = int(params[0])
        qty = int(params[1]) if len(params) > 1 else 1
    except (IndexError, ValueError):
        return None
    # never drop a class item (its Quantity is class points, not a stack) — P2-1
    if _is_class_item(conn, item_id):
        return None
    # skip EQUIPPED rows (worn on the avatar) and prefer plain over gemmed copies, so a delete
    # can't destroy an equipped piece or clobber a gem roll while a plain copy exists.
    row = conn.execute(
        "SELECT * FROM char_items WHERE item_id=? AND char_id=? AND banked=0 AND equipped=0 "
        "ORDER BY (pattern_json IS NOT NULL), char_item_id LIMIT 1", (item_id, char["id"])).fetchone()
    if row is None:
        return None
    remaining = int(row["quantity"]) - qty
    if remaining > 0:
        conn.execute("UPDATE char_items SET quantity=? WHERE char_item_id=?",
                     (remaining, row["char_item_id"]))
    else:
        conn.execute("DELETE FROM char_items WHERE char_item_id=?", (row["char_item_id"],))
    conn.commit()
    return int(row["char_item_id"])


def _equip_spot(conn, item_id):
    item = db.item(conn, item_id)
    if item is None:
        return None, None
    return item, int(item.get("EquipSpot", 0) or 0)


def equip_item(conn, char, item_id):
    """equipItem [itemID]: equip an owned item. Persists char_items.equipped (one item
    per EquipSpot) and returns the s2c equipItem (ResponseEquipItem) that updates the
    avatar live. None if the item isn't in our catalog."""
    try:
        item_id = int(item_id)
    except (TypeError, ValueError):
        return None
    item, spot = _equip_spot(conn, item_id)
    if item is None:
        return None

    # unequip anything currently in this EquipSpot, then equip this item (if owned)
    for r in conn.execute(
            "SELECT ci.char_item_id, ci.item_id FROM char_items ci "
            "WHERE ci.char_id=? AND ci.equipped=1", (char["id"],)):
        _, other_spot = _equip_spot(conn, r["item_id"])
        if other_spot == spot:
            conn.execute("UPDATE char_items SET equipped=0 WHERE char_item_id=?",
                         (r["char_item_id"],))
    conn.execute(
        "UPDATE char_items SET equipped=1 WHERE char_item_id=("
        "SELECT char_item_id FROM char_items WHERE char_id=? AND item_id=? AND banked=0 "
        "ORDER BY char_item_id LIMIT 1)", (char["id"], item_id))
    conn.commit()

    equipped_item = {"ID": item_id, "Bundle": item.get("Bundle"),
                     "ClassParticleBundle": item.get("ClassParticleBundle"),
                     "PrefabName": item.get("PrefabName"), "EquipSpot": spot,
                     "ItemType": item.get("ItemType"), "Scale": item.get("Scale"),
                     "OffsetX": item.get("OffsetX"), "OffsetY": item.get("OffsetY")}
    # A CLASS armor's eqp entry must be the class RIG, not the stripped catalog item — the
    # catalog rows carry no ClassParticleBundle, and the client sets Player.classBundle from
    # this very packet (Player.updateData reads eqp[Class].ClassParticleBundle). Serving the
    # raw item nulled the bundle and silently killed ALL skill particles/aura VFX until the
    # next login (which goes through build_init_player and applies the rig). Same source
    # of truth as login.
    if spot == EQUIP_SPOT_CLASS:
        cls_id = forge.class_for_armor_item(conn, item_id)
        crow = conn.execute("SELECT rig FROM classes WHERE class_id=?",
                            (cls_id,)).fetchone() if cls_id is not None else None
        try:
            rig = json.loads(crow["rig"]) if crow and crow["rig"] else None
        except (TypeError, ValueError):
            rig = None
        if rig:
            equipped_item.update(rig)
    return {"Cmd": "equipItem", "equippedItem": equipped_item,
            "player": f"p:{uid_for(char)}", "equipSpot": spot}


# EquipSpots enum (client): Weapon=2 and Class=6 are always required and can't be unequipped;
# everything else (Head=3, Back=4, Pet=5, Armor=7, Amulet=10, ...) is removable.
EQUIP_SPOT_WEAPON = 2
EQUIP_SPOT_CLASS = 6


def unequip_item(conn, char, item_id):
    """unequipItem [itemID]: clear char_items.equipped for an equipped item. Returns its EquipSpot
    int on success, or None (item not owned/equipped, unknown, or a required Weapon/Class spot)."""
    try:
        item_id = int(item_id)
    except (TypeError, ValueError):
        return None
    _item, spot = _equip_spot(conn, item_id)
    if spot is None or spot in (EQUIP_SPOT_WEAPON, EQUIP_SPOT_CLASS):
        return None
    row = conn.execute(
        "SELECT char_item_id FROM char_items WHERE char_id=? AND item_id=? AND equipped=1 "
        "AND banked=0 ORDER BY char_item_id LIMIT 1", (char["id"], item_id)).fetchone()
    if row is None:
        return None
    conn.execute("UPDATE char_items SET equipped=0 WHERE char_item_id=?", (row["char_item_id"],))
    conn.commit()
    return spot
