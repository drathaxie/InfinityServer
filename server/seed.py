"""
Seed the database catalog from captured samples (idempotent).

- Shops + shop items come from every loadShop sample we have.
- The initPlayer template (full, known-good 263KB player object) is used as the
  starting loadout for brand-new characters so the avatar renders correctly;
  identity, gold, and inventory are then overridden per-account at login.

Seeding is INSERT-IF-ABSENT: the catalog (shops, items, maps, apops, monsters, classes,
cutscenes, quests) is seeded only for rows that don't exist yet, so a re-run (e.g. every
service restart) NEVER clobbers content edited in-game — the DB is the live source of truth,
data/ is just the initial baseline. Character data is never touched by seeding.
"""
import json
import pathlib

import db
import fill_dev_shop
import montemplates

DATA = pathlib.Path(__file__).resolve().parent.parent / "data"
MAPS_DIR = DATA / "maps"
QUESTS_FILE = DATA / "quests.json"
APOPS_FILE = DATA / "apops.json"
CUTSCENES_FILE = DATA / "cutscenes.json"
DEFAULTCLASSES_FILE = DATA / "defaultclasses.json"
MONSTER_DROPS_FILE = DATA / "monster_drops.json"
ITEMS_FILE = DATA / "items.json"
REDEEM_CODES_FILE = DATA / "redeem_codes.json"
SHOPS_FILE = DATA / "shops.json"
BASECLASSES_FILE = DATA.parent / "capture" / "harvest" / "baseclasses_live.json"


def seed_hairs(conn):
    """Seed the canonical HairInfo catalog used by creation and changeColor."""
    try:
        payload = json.loads(BASECLASSES_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return 0
    count = 0
    for hair in payload.get("hairs", []):
        if isinstance(hair, dict) and hair.get("ID") is not None:
            db.store_hair(conn, hair)
            count += 1
    return count


def seed_redeem_codes(conn):
    """Install versioned promo-code definitions, exclusive items, and exact reward bundles."""
    try:
        definitions = json.loads(REDEEM_CODES_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return 0
    count = 0
    for code, definition in definitions.items():
        code = str(code).strip().upper()
        if not code:
            continue
        # A promo can carry complete item definitions for bundle-only art that does not have a
        # canonical ItemID yet. Replace the synthetic placeholder deterministically so restarts
        # and fresh installs preserve the public name and render metadata used by the reward.
        for item in definition.get("itemDefinitions", []):
            if isinstance(item, dict) and int(item.get("ID", 0) or 0) > 0:
                db.store_item(conn, item, replace=True)
        conn.execute(
            "INSERT INTO redeem_codes(code,description,max_uses,active,created) VALUES(?,?,?,?,0) "
            "ON CONFLICT(code) DO UPDATE SET description=excluded.description,"
            "max_uses=excluded.max_uses,active=excluded.active",
            (code, definition.get("description", ""), int(definition.get("maxUses", 0) or 0),
             1 if definition.get("active", True) else 0))
        conn.execute("DELETE FROM redeem_code_rewards WHERE LOWER(code)=LOWER(?)", (code,))
        for reward in definition.get("rewards", []):
            conn.execute(
                "INSERT INTO redeem_code_rewards(code,reward_type,reward_value,reward_qty,reward_field) "
                "VALUES(?,?,?,?,?)", (code, reward.get("type"), int(reward.get("value", 0)),
                                      int(reward.get("quantity", 1) or 1),
                                      reward.get("field", "ip25")))
        count += 1
    return count



def _seed_shop(conn, shop_obj):
    """Seed one shop into the normalized tables: the shop's metadata (with an
    empty items array), each item into the shared `items` catalog, and a lean
    `shop_items` row linking the two. Items are deduped across shops by item_id."""
    has_wrapper = isinstance(shop_obj, dict) and "shop" in shop_obj
    shop = shop_obj.get("shop") if has_wrapper else shop_obj
    if not isinstance(shop, dict):
        return 0
    shop_id = int(shop.get("shopID", 0))
    items = shop.get("items") or []

    db.store_shop(conn, shop_obj, shop_id=shop_id)   # shop meta in canonical columns (items dropped)

    n = 0
    for it in items:
        item_id = int(it.get("ID", 0))
        db.store_item(conn, it)             # catalog row in canonical columns (insert-if-absent)
        qremain = it.get("QuantityRemain")
        conn.execute(
            "INSERT INTO shop_items(shop_id, shop_item_id, item_id, cost, coins, "
            "quantity_remain) VALUES(?,?,?,?,?,?) "
            "ON CONFLICT(shop_id, shop_item_id) DO NOTHING",
            (
                shop_id,
                int(it.get("ShopItemID", 0)),
                item_id,
                int(it.get("Cost", 0) or 0),
                1 if it.get("Coins") else 0,
                int(qremain) if qremain is not None else -1,
            ),
        )
        n += 1
    return n


def seed_maps(conn):
    """Map/Area catalog from the mined data/maps/*.json. Stores both the AreaJoin metadata and
    the FULL served map doc ({area,cells}) in maps.doc — the DB is the authoritative, editable
    source now that R2 is gone. Keyed by str_map_name = the file stem (maps.py looks it up there)."""
    n = 0
    for f in MAPS_DIR.glob("*.json"):
        try:
            doc = json.loads(f.read_text(encoding="utf-8")) or {}
        except Exception:
            continue
        area = doc.get("area")
        if not area:
            continue
        stem = "".join(ch if ch.isalnum() else "_" for ch in f.stem).lower()
        meta = {k: v for k, v in area.items() if k not in ("monBranch", "uoBranch")}
        conn.execute(
            "INSERT INTO maps(map_id, area_name, str_map_name, display_name, prefab_name, "
            "soundtrack_id, int_type, bundle, quest_ids, raw, doc) VALUES(?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(map_id) DO NOTHING",
            (int(area.get("areaId", 0) or 0), area.get("areaName"), stem,
             area.get("DisplayName"), area.get("PrefabName"), area.get("SoundtrackID"),
             area.get("intType"),
             json.dumps(area.get("Bundle"), separators=(",", ":")) if area.get("Bundle") else None,
             json.dumps(area.get("QuestIDs") or [], separators=(",", ":")),
             json.dumps(meta, separators=(",", ":")),
             json.dumps(doc, separators=(",", ":"))),
        )
        n += 1
    return n


def _seed_quest(conn, q):
    qid = int(q.get("QuestID"))
    if conn.execute("SELECT 1 FROM quests WHERE quest_id=?", (qid,)).fetchone():
        return                              # seed only when absent — preserve edits + turnins/rewards
    conn.execute(
        "INSERT INTO quests(quest_id, name, descr, end_text, faction_id, class_name, "
        "prev_quest, map_id, dialog_id, apop_id, turnin_type, notification_type, "
        "reward_count, turnin_map_id, turnin_npc_id, turnin_frame, turnin_pad, raw) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT(quest_id) DO UPDATE SET name=excluded.name, descr=excluded.descr, "
        "end_text=excluded.end_text, faction_id=excluded.faction_id, "
        "class_name=excluded.class_name, prev_quest=excluded.prev_quest, "
        "map_id=excluded.map_id, dialog_id=excluded.dialog_id, apop_id=excluded.apop_id, "
        "turnin_type=excluded.turnin_type, notification_type=excluded.notification_type, "
        "reward_count=excluded.reward_count, turnin_map_id=excluded.turnin_map_id, "
        "turnin_npc_id=excluded.turnin_npc_id, turnin_frame=excluded.turnin_frame, "
        "turnin_pad=excluded.turnin_pad, raw=excluded.raw",
        (qid, q.get("Name"), q.get("Desc"), q.get("EndText"), q.get("FactionID"),
         q.get("ClassName"), q.get("prevQuest"), q.get("MapID"), q.get("DialogID"),
         q.get("ApopID"), q.get("TurnInType"), q.get("NotificationType"),
         q.get("RewardCount"), q.get("TurnInMapID"), q.get("TurnInNPCID"),
         q.get("TurnInFrame"), q.get("TurnInPad"), json.dumps(q, separators=(",", ":"))),
    )
    db.store_quest_turnins(conn, qid, q.get("turnin"))   # lossless objective rows (qturninrecord)
    conn.execute("DELETE FROM quest_rewards WHERE quest_id=?", (qid,))
    idx = 0
    rewards = q.get("Rewards") or {}
    for kind, lst in (rewards.items() if isinstance(rewards, dict) else []):
        for r in (lst or []):
            if not isinstance(r, dict):
                continue
            conn.execute(
                "INSERT INTO quest_rewards(quest_id, idx, kind, item_id, quantity) "
                "VALUES(?,?,?,?,?)",
                (qid, idx, kind, r.get("ItemID") or r.get("ID"), r.get("Quantity", 1)),
            )
            idx += 1


def seed_quests(conn):
    """Quest catalog (+turnins, rewards) from the mined data/quests.json."""
    if not QUESTS_FILE.exists():
        return 0
    try:
        quests = json.loads(QUESTS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return 0
    for q in quests.values():
        if isinstance(q, dict) and q.get("QuestID") is not None:
            _seed_quest(conn, q)
    return len(quests)


def seed_apops(conn):
    """Apop catalog from the mined data/apops.json (apopID -> apop object)."""
    if not APOPS_FILE.exists():
        return 0
    try:
        apops = json.loads(APOPS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return 0
    for aid, a in apops.items():
        conn.execute(
            "INSERT INTO apops(apop_id, name, raw) VALUES(?,?,?) "
            "ON CONFLICT(apop_id) DO NOTHING",
            (int(aid), a.get("name") if isinstance(a, dict) else None,
             json.dumps(a, separators=(",", ":"))))
    return len(apops)


def seed_cutscenes(conn):
    """Captured AE cutscenes (id -> Dialogger JsonText) from data/cutscenes.json. These play via
    getDialog; the Dialogger editor adds/edits more at runtime (DialoggerSave). Only what we've
    captured so far (1=1); more arrive as we capture them."""
    if not CUTSCENES_FILE.exists():
        return 0
    try:
        scenes = json.loads(CUTSCENES_FILE.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return 0
    for cid, jsontext in scenes.items():
        conn.execute("INSERT INTO cutscenes(id, raw) VALUES(?,?) "
                     "ON CONFLICT(id) DO NOTHING",
                     (int(cid), jsontext))
    return len(scenes)


CLASSES_FILE = DATA / "classes.json"
CLASS_RIGS_FILE = DATA / "class_rigs.json"

# P2-2: real captured base-class ClassIDs -> migrate the old invented placeholders. Mined from
# the capture by correlating each initPlayer's playerInfo.ClassID with its user.sClass:
#   Healer ClassID 17 (sClass "Healer", item 15651), Warrior ClassID 33 (sClass "Warrior",
#   item 15654), Dragonslayer 1932 (already real). Mage (item 15653) NEVER appears in the
#   capture (the account didn't play it), so its real ClassID is UNKNOWN — it keeps the
#   placeholder 2 (data/classes.json) until a targeted capture provides it.
REAL_CLASS_IDS = {1: 17, 3: 33}        # old placeholder -> real captured ClassID

# Five Flex skills lost their regMana field in the mined SkillData even though
# their captured descriptions state the requirement explicitly. Store the wire
# convention (negative = cost). Applied once and only while the existing value
# is zero, so later SkillForge tuning is not repeatedly clobbered.
TOOLTIP_MANA_COST_FIX_VERSION = 1
TOOLTIP_MANA_COST_FIXES = {
    117: -10,  # Warrior: Prepared Strike
    118: -15,  # Warrior: On Guard
    142: -20,  # Healer: Healing Word
    133: -15,  # Rogue: Stiletto
    132: -5,   # Rogue: Footwork
}


def seed_classes(conn):
    """Seed classes + their slotted skills + the shared skill library from the
    mined data/classes.json. INSERT-IF-ABSENT: existing classes/slots/skills are left
    untouched, so re-running keeps in-client Forge edits (those write to the same tables)."""
    if not CLASSES_FILE.exists():
        return (0, 0)
    try:
        classes = json.loads(CLASSES_FILE.read_text(encoding="utf-8"))
    except Exception:
        return (0, 0)
    try:
        rigs = json.loads(CLASS_RIGS_FILE.read_text(encoding="utf-8")) if CLASS_RIGS_FILE.exists() else {}
    except Exception:
        rigs = {}
    global _class_item_defs
    try:
        _class_item_defs = json.loads(CLASS_ITEM_DEFS_FILE.read_text(encoding="utf-8")) \
            if CLASS_ITEM_DEFS_FILE.exists() else {}
    except Exception:
        _class_item_defs = {}
    # P2-2: remap the invented placeholder base-class IDs to the REAL captured ClassIDs before
    # seeding. From the same initPlayer's playerInfo.ClassID + user.sClass: Healer 1->17,
    # Warrior 3->33 (DS 1932 already real; Mage's real ClassID isn't in the capture — the
    # capture account never played Mage — so it keeps placeholder 2). One-time + idempotent:
    # once the old ids are gone the remap matches nothing. Updates chars + cascades class_skills.
    for old, new in REAL_CLASS_IDS.items():
        if conn.execute("SELECT 1 FROM classes WHERE class_id=?", (old,)).fetchone():
            conn.execute("UPDATE characters SET class_id=? WHERE class_id=?", (new, old))
            conn.execute("DELETE FROM classes WHERE class_id=?", (old,))   # cascades class_skills
    n_cls = n_skills = 0
    for name, c in classes.items():
        cid = int(c["ID"])
        rig = rigs.get(name)
        res = c.get("Resource")                     # per-class resource bar model (P0-2)
        conn.execute(
            "INSERT INTO classes(class_id, name, bundle, rig, resource) VALUES(?,?,?,?,?) "
            "ON CONFLICT(class_id) DO NOTHING",
            (cid, name, c.get("Bundle", ""),
             json.dumps(rig, separators=(",", ":")) if rig else None,
             json.dumps(res, separators=(",", ":")) if res else None))
        n_cls += 1
        # the class ARMOR item (equip it to become this class) -> catalog, from the REAL
        # captured item def (full shape: Icon/Description/Filename/Bundle/...) so it renders
        # like any other item. NEVER clobber an existing catalog row.
        if rig and rig.get("ID"):
            iid = int(rig["ID"])
            real = _class_item_defs.get(str(iid)) or _class_item_defs.get(iid)
            if real:
                # The captured def's Quantity (302500) is a specific char's class POINTS (rank),
                # not a catalog property — it leaked in and made the shop show 302500 (P2-1).
                # Class points are per-owned-instance (char_items.quantity); the catalog/shop
                # listing is just the purchase quantity (1, per the captured shop sample).
                r2 = dict(real)
                r2["ID"] = iid
                r2["Name"] = real.get("Name") or real.get("sName") or name
                r2["ItemType"] = int(real.get("ItemType", 21) or 21)
                r2["Quantity"] = 1          # catalog purchase qty, not the captured class points
                db.store_item(conn, r2)
        defs = c.get("_skilldefs", {})
        for slot_s, skill_id in (c.get("Skills") or {}).items():
            slot, sid = int(slot_s), int(skill_id)
            conn.execute(
                "INSERT INTO class_skills(class_id, slot, skill_id) VALUES(?,?,?) "
                "ON CONFLICT(class_id, slot) DO NOTHING",
                (cid, slot, sid))
            # Only seed library metadata if the skill is new — never clobber an
            # authored node-graph (data/forge_data) the Forge later wrote.
            if conn.execute("SELECT 1 FROM skills WHERE skill_id=?", (sid,)).fetchone():
                continue
            meta = defs.get(str(sid), {})
            conn.execute(
                "INSERT INTO skills(skill_id, action, name, description, icon, slot, "
                "auto_h_range, auto_v_range, auto_hold_at_range, mana) "
                "VALUES(?,?,?,?,?,?,?,?,?,?)",
                (sid, int(meta.get("act", 0) or 0), meta.get("nam"), meta.get("desc"),
                 meta.get("icon"), slot, float(meta.get("autoHRange", 0) or 0),
                 float(meta.get("autoVRange", 0) or 0), 1 if meta.get("autoHoldAtRange") else 0,
                 int(meta.get("regMana", 0) or 0)))
            n_skills += 1
    mana_fix = conn.execute(
        "SELECT v FROM kv WHERE k='tooltip_mana_cost_fix_version'").fetchone()
    stored_mana_fix = int(mana_fix["v"]) if mana_fix and str(mana_fix["v"]).isdigit() else 0
    if stored_mana_fix < TOOLTIP_MANA_COST_FIX_VERSION:
        for sid, reg_mana in TOOLTIP_MANA_COST_FIXES.items():
            conn.execute("UPDATE skills SET mana=? WHERE skill_id=? AND COALESCE(mana,0)=0",
                         (reg_mana, sid))
        conn.execute(
            "INSERT INTO kv(k,v) VALUES('tooltip_mana_cost_fix_version',?) "
            "ON CONFLICT(k) DO UPDATE SET v=excluded.v",
            (str(TOOLTIP_MANA_COST_FIX_VERSION),))
    return (n_cls, n_skills)


# The Dragonslayer Auto Attack (skill 165), reconstructed from the captured resolved
# Attack sequence (Range -> Cooldown -> SoundFX -> Damage -> PlayerAnimation -> 3 class
# particles -> Resource -> UpdateAnimation). Authored-field form; combat._render_node
# turns each into the resolved node the client plays.
_DS_AUTO_NODES = [
    ("0", {"Name": "OnRequest"}),
    ("1", {"Name": "Range", "HRange": 5, "VRange": 1}),
    ("2", {"Name": "Cooldown", "CD": 2000}),
    ("3", {"Name": "SoundFX", "Sound": "sfx_warrior_aa",
           "Animation": "Attack1_Auto,Attack2,Attack3"}),
    ("4", {"Name": "Damage", "DamageType": "Physical", "Multiplier": 1}),
    ("5", {"Name": "PlayerAnimation", "Animation": "Attack1_Auto,Attack2,Attack3"}),
    ("6", {"Name": "Particle", "Particle": "classDragonslayer_S1_P1",
           "Animation": "Attack1_Auto", "X": -2, "Y": 3}),
    ("7", {"Name": "Particle", "Particle": "classDragonslayer_S1_P2",
           "Animation": "Attack2", "X": -1, "Y": 3}),
    ("8", {"Name": "Particle", "Particle": "classDragonslayer_S1_P3",
           "Animation": "Attack3", "X": 0, "Y": 3}),
    ("9", {"Name": "Resource", "Amount": 5}),
    ("10", {"Name": "UpdateAnimation", "Value": "2H_Fight"}),
]


# The rest of the Dragonslayer kit, reconstructed from their captured Attack sequences
# (cooldowns, animations, sounds, particles, hitbox dims, restricts, auras all real;
# Damage Multiplier and Resource grants are ours since the server computes those).
_DS_SCORCHED = [                                        # 167, slot 1: melee hitbox + DoT-less
    ("0", {"Name": "OnRequest"}),
    ("1", {"Name": "Range", "HRange": 5, "VRange": 1}),
    ("2", {"Name": "Cooldown", "CD": 3000}),
    ("3", {"Name": "Restrict", "Movement": True, "Skills": True, "Slot": "2,3,4,5",
           "Animation": "DS Skill1 Auto Reset", "Duration": 0.3}),
    ("4", {"Name": "Interruptable", "Animation": "DS Skill1 Auto Reset", "Time": 0.5}),
    ("5", {"Name": "SoundFX", "Sound": "SFX_DragonSlayer_1C",
           "Animation": "DS Skill1 Auto Reset", "Time": 0.2}),
    ("6", {"Name": "Particle", "Particle": "classDragonslayer_S2_P1",
           "Animation": "DS Skill1 Auto Reset", "X": -4, "Y": 2}),
    # no PlayerAnimation: the body animation is driven by AnimationHitbox.Input (the real
    # graphs carry no PlayerAnimation — capture Attack batch is Range..Particle, then the
    # AnimationHitbox igai animates + reports hits). A PlayerAnimation here would double up.
    ("7", {"Name": "AnimationHitbox", "X": 7, "Y": 0, "Width": 12, "Height": 2,
           "Animation": "DS Skill1 Auto Reset", "Speed": 0.75, "Time": 0.1}),
    ("8", {"Name": "Damage", "DamageType": "Physical", "Multiplier": 1.5}),
    ("9", {"Name": "DispenseDamage"}),
    ("10", {"Name": "Resource", "Amount": 10}),
    ("11", {"Name": "UpdateAnimation", "Value": "2H_Fight"}),
]
_DS_IMPALE = [                                          # 103, slot 2: long reach, pull, Bleeding
    ("0", {"Name": "OnRequest"}),
    ("1", {"Name": "Range", "HRange": 22, "VRange": 1}),
    ("2", {"Name": "Cooldown", "CD": 6000}),
    ("3", {"Name": "Restrict", "Movement": True, "Skills": True, "Slot": "1,2,3,4,5",
           "Animation": "DS Skill2", "Duration": 0.4}),
    ("4", {"Name": "Interruptable", "Animation": "DS Skill2", "Time": 0.4}),
    ("5", {"Name": "SoundFX", "Sound": "SFX_DragonSlayer_2A", "Animation": "DS Skill2"}),
    ("6", {"Name": "Particle", "Particle": "classDragonslayer_S3_P1",
           "Animation": "DS Skill2", "X": 12, "Y": 2, "Time": 0.22}),
    # animation via AnimationHitbox.Input (see Scorched note) — no PlayerAnimation
    ("7", {"Name": "AnimationHitbox", "X": 10, "Y": 0, "Width": 20, "Height": 4,
           "Animation": "DS Skill2", "Speed": 1, "Time": 0.22}),
    ("8", {"Name": "MoveTargets", "Mode": "Pull", "Distance": 6}),
    ("9", {"Name": "Damage", "DamageType": "Physical", "Multiplier": 2}),
    ("10", {"Name": "DispenseDamage"}),
    ("11", {"Name": "Aura", "AuraName": "Bleeding", "Duration": 3}),
    ("12", {"Name": "Resource", "Amount": 10}),
    ("13", {"Name": "UpdateAnimation", "Value": "2H_Fight"}),
]
_DS_INCAP = [                                           # 104, slot 3: leap (DashToTarget), Weakened
    ("0", {"Name": "OnRequest"}),
    ("1", {"Name": "Range", "HRange": 40, "VRange": 40}),
    ("2", {"Name": "Cooldown", "CD": 10000}),
    ("3", {"Name": "Restrict", "Movement": True, "Skills": True, "Slot": "1,2,3,4,5",
           "Animation": "DS Skill3", "Duration": 0.35}),
    ("4", {"Name": "Interruptable", "Animation": "DS Skill3", "Time": 0.35}),
    ("5", {"Name": "SoundFX", "Sound": "SFX_DragonSlayer_3Full", "Animation": "DS Skill3"}),
    ("6", {"Name": "Particle", "Particle": "classDragonslayer_S4_P1",
           "Animation": "DS Skill3", "X": 16, "Y": 6, "Time": 0.3}),
    ("7", {"Name": "DashToTarget", "Animation": "DS Skill3"}),
    # animation via AnimationHitbox.Input (see Scorched note) — no PlayerAnimation
    ("8", {"Name": "AnimationHitbox", "X": 6, "Y": 0, "Width": 12, "Height": 2,
           "Animation": "DS Skill3", "Speed": 0.75, "Time": 0.3}),
    ("9", {"Name": "Damage", "DamageType": "Physical", "Multiplier": 1.5}),
    ("10", {"Name": "DispenseDamage"}),
    ("11", {"Name": "Aura", "AuraName": "Weakened", "Duration": 5}),
    ("12", {"Name": "Resource", "Amount": 10}),
    ("13", {"Name": "UpdateAnimation", "Value": "2H_Fight"}),
]
_DS_BANE = [                                            # 105, slot 4: self-buff (no hitbox)
    ("0", {"Name": "OnRequest"}),
    ("1", {"Name": "Cooldown", "CD": 25000}),
    ("2", {"Name": "Restrict", "Movement": True, "Skills": True, "Slot": "2,3,4,5",
           "Animation": "DS Skill5", "Duration": 0.3}),
    ("3", {"Name": "Interruptable", "Animation": "DS Skill5", "Time": 0.3}),
    ("4", {"Name": "SoundFX", "Sound": "SFX_DragonSlayer_5A", "Animation": "DS Skill5"}),
    ("5", {"Name": "Particle", "Particle": "classDragonslayer_S5_P1",
           "Animation": "DS Skill5", "X": 0, "Y": 12}),
    ("6", {"Name": "PlayerAnimation", "Animation": "DS Skill5"}),
    ("7", {"Name": "Aura", "AuraName": "Dragonbane"}),
    ("8", {"Name": "AuraVFX", "AuraName": "Dragonbane", "VFX": "classDragonSlayer_DSPowerAura"}),
    ("9", {"Name": "UpdateAnimation", "Value": "2H_Fight"}),
]


# Bump when the canonical graphs change (DS five below OR data/skill_graphs.json) — that
# forces a one-time re-apply over the previously-seeded versions (without clobbering later
# Forge edits, which only happen once the version stops advancing).
#   v4: Healing Word (142) re-authored as a HEAL (negative Damage on allies/self) — P0-1.
#   v5: DS hitbox skills (Scorched/Impale/Incap) drop the PlayerAnimation workaround — the
#       real AnimationHitbox handshake (P1-1) animates via AnimationHitbox.Input.
#   v6: per-class element + damage multipliers authored on the mined graphs (P1-4): Mage/Healer
#       Magical (scales on sp/INT), Warrior Physical; multipliers by role (auto 1, nuke ~2).
#   v7: monster skills — Ragnafluff's telegraphed "Ruinous Bars" tile skill (HitTiles vertical
#       rectangles), so the SkillForge edits monster classes and the AI casts them (gmah).
#   v8: Ragnafluff's REAL three-skill rotation lifted from a live capture — thin HitTiles bars,
#       a scanning TileTrack Cross, and 4 HitStream firewalls (replaces the oversized v7 bars).
#   v9: Ragnafluff tile damage tuned down (mult 1.5->1.0) to pair with the combat per-player tile
#       rate-cap — the firewalls' enter/exit reports were stacking into a one-shot.
#   v10: Ragnafluff "Ruinous Echoes" — summons 2 Ragnafluff Clones (server-side spawnMob, real
#        fightable adds) on an 18s rotation slot, from the captured clone spawn.
#   v15: the Abomilich fight (InfinityLichBoss 429/430) — AE built and placed this boss but never
#        gave it a moveset. Seven skills across the whole captured tile vocabulary (HitTiles,
#        TileWave, TileCluster, TileSafe, TileTrack, HitStream, Summon); see _ABOMILICH_SKILLS
#        for exactly which parts are capture-verified and which are ours.
SKILL_GRAPH_VERSION = 15


# Authored per-skill element + damage multiplier (P1-4). NEITHER is minable — a resolved Damage
# node carries only computed Damages/TargetHPs, never the authored element or multiplier (both
# server-internal). So, like the DS five, these are HAND-AUTHORED design values: Mage/Healer cast
# Magical (combat scales Magical damage on sp -> INT/WIS); Warrior is Physical (ap -> STR/DEX).
# Multipliers are by role (auto 1.0, single-target nuke ~2.0, AoE/filler ~1.5) — ours, not AE's.
SKILL_DAMAGE = {
    # Mage — Magical
    135: ("Magical", 1.0),    # Magic Missile (auto)
    136: ("Magical", 2.0),    # Fireball (nuke + Scorched DoT)
    137: ("Magical", 2.0),    # Ice Shard (nuke + Frozen Blood)
    138: ("Magical", 1.5),    # Explosion (AoE up to 4)
    # Healer — Magical offensive (142 Healing Word is a HEAL, left untouched)
    140: ("Magical", 1.0),    # Auto Attack
    141: ("Magical", 1.5),    # Heartbeat (damage + % current HP)
    143: ("Magical", 1.0),    # Energy Flow (debuff + minor damage)
    144: ("Magical", 2.0),    # Holy (smite)
    # Warrior — Physical
    114: ("Physical", 1.0),   # Auto Attack
    115: ("Physical", 1.75),  # Decisive Strike (hitbox cleave)
    116: ("Physical", 1.5),   # Imbalancing Strike (stun)
    117: ("Physical", 1.5),   # Prepared Strike
}


def _author_damage(data, element, mult):
    """Set DamageType/Multiplier on the OFFENSIVE Damage nodes of a mined graph (P1-4). Heals
    (Damage{Heal:true}) are left alone. Returns the (mutated copy of) data graph."""
    data = json.loads(json.dumps(data))                  # don't mutate the shared mined dict
    if isinstance(data, list) and len(data) > 1 and isinstance(data[1], dict):
        for nd in data[1].values():
            if isinstance(nd, dict) and nd.get("Name") == "Damage" and not nd.get("Heal"):
                nd["DamageType"] = element
                nd["Multiplier"] = mult
    return data


MAGE_SPELL_FX = {
    135: ("classMage_S1_P1", "classMage_S1_P2"),
    136: ("classMage_S2_P1", "classMage_S2_P2"),
    137: ("classMage_S3_P1", "classMage_S3_P2"),
    138: ("classMage_S4_P1", "classMage_S4_P2"),
}
MAGE_SPELL_FX_VERSION = 1


def _author_mage_fx(data, skill_id):
    """Fill only blank Mage SpellAnimation projectile fields; preserve Forge customization."""
    fx = MAGE_SPELL_FX.get(skill_id)
    if not fx:
        return data
    data = json.loads(json.dumps(data))
    if isinstance(data, list) and len(data) > 1 and isinstance(data[1], dict):
        for node in data[1].values():
            if isinstance(node, dict) and node.get("Name") == "SpellAnimation":
                defaults = {"FX": "ORIGIN", "SpellGraphic": fx[0], "SpellImpact": fx[1],
                            "AttachInit": "CastAttach", "Attach": "Cast",
                            "AttachImpact": "Origin", "Follow": True}
                for key, value in defaults.items():
                    if node.get(key) in (None, ""):
                        node[key] = value
    return data


def _patch_mage_spell_fx(conn):
    """One-time additive visual repair over live Mage graphs without refreshing other skills."""
    row = conn.execute("SELECT v FROM kv WHERE k='mage_spell_fx_version'").fetchone()
    stored = int(row["v"]) if row and str(row["v"]).isdigit() else 0
    if stored >= MAGE_SPELL_FX_VERSION:
        return 0
    changed = 0
    for skill_id in MAGE_SPELL_FX:
        skill = conn.execute("SELECT data FROM skills WHERE skill_id=?", (skill_id,)).fetchone()
        if not skill or not skill["data"]:
            continue
        try:
            before = json.loads(skill["data"])
        except (TypeError, ValueError):
            continue
        after = _author_mage_fx(before, skill_id)
        if after != before:
            conn.execute("UPDATE skills SET data=? WHERE skill_id=?",
                         (json.dumps(after, separators=(",", ":")), skill_id))
            changed += 1
    conn.execute("INSERT INTO kv(k,v) VALUES('mage_spell_fx_version',?) "
                 "ON CONFLICT(k) DO UPDATE SET v=excluded.v",
                 (str(MAGE_SPELL_FX_VERSION),))
    return changed


SKILL_GRAPHS_FILE = DATA / "skill_graphs.json"   # mined per-skill graphs (all classes)
CLASS_ITEM_DEFS_FILE = DATA / "class_item_defs.json"   # real captured class-armor item defs
_class_item_defs = {}

# Class items are ranked by class POINTS held in the owned instance's Quantity (InventoryItem.cs:
# `classRank = new Rank(Quantity)`; Inventory.hasClassPoints gates skills on `Quantity >= points`).
# Rank.cs caps at 302500 (max rank). We grant the MAX so every class is immediately playable —
# class-point PROGRESSION (earning CP to rank up) is a future mechanic; until then a consistent
# maxed CP is the faithful, non-corrupt state (vs the live 1 / 302499 / 302500 split). (P2-1)
CLASS_CP_MAX = 302500

# STAFF-ONLY class armors: granted only to access >= STAFF_CLASS_ACCESS, and stripped from anyone
# below it. Big Jake (class 9100 / armor item 91000) is a staff/dev class, not a player class.
STAFF_ONLY_CLASS_ITEMS = {91000}
STAFF_CLASS_ACCESS = 40
BIG_JAKE_CLASS_ID = 9100
STARTER_CLASS_ID = 33                  # Warrior — fall back to it if a stripped class was equipped


def class_item_ids(conn):
    """The class-armor item ids (from each class's rig.ID) that exist in the catalog."""
    ids = []
    for r in conn.execute("SELECT rig FROM classes WHERE rig IS NOT NULL"):
        try:
            iid = (json.loads(r["rig"]) or {}).get("ID")
        except Exception:
            iid = None
        if iid is not None and conn.execute("SELECT 1 FROM items WHERE item_id=?",
                                            (int(iid),)).fetchone():
            ids.append(int(iid))
    return ids


# --- monster skills ----------------------------------------------------------------------
# Ragnafluff The Ruinous gets a class + its three telegraphed tile skills, so the SkillForge edits
# it (every class is listed there) and the AI rotates through them. The class has no rig/resource
# -> a pure skill holder, never equippable by a player. The two captured MonIDs share it. All node
# params below are LIFTED EXACTLY from a live-AE packet capture of the fight (MonReq payloads), so
# the shapes/speeds/scales/sounds match the real boss. The per-skill Cooldown is the cadence-TO-
# the-next cast in the rotation (measured from the capture's ReqTS gaps: bars->cross ~4.3s,
# cross->firewalls ~5s, firewalls->bars ~7s). Damage.Multiplier is ours (the capture only carries
# the telegraph; damage is applied server-side on the gmah hit report).
RAGNAFLUFF_CLASS_ID = 9364
RAGNAFLUFF_MON_IDS = (364, 372)

# slot -> (skill_id, name, description, node_list). Each node_list is a linear graph; the tile
# node(s) become MonReq Responses. "Ruinous Firewalls" carries all FOUR HitStream walls — they
# fire together as one cast (shared ReqTS), exactly as captured.
_RAGNAFLUFF_SKILLS = [
    (0, 90364, "Ruinous Bars", "Searing bars sweep the arena — step out of the red.", [
        ("0", {"Name": "OnRequest"}),
        ("1", {"Name": "Cooldown", "CD": 4500}),
        ("2", {"Name": "HitTiles", "Shape": "Rectangle", "VFX": "", "Speed": 1.8,
               "ScaleX": 0.6, "ScaleY": 3.0, "FinishAnimation": "Attack2",
               "ImpactSound": "sfx_healer_impact"}),
        ("3", {"Name": "Damage", "DamageType": "Fire", "Multiplier": 1.0}),
    ]),
    (1, 90365, "Ruinous Cross", "Lances scan the room and converge in a cross — don't be center.", [
        ("0", {"Name": "OnRequest"}),
        ("1", {"Name": "Cooldown", "CD": 5000}),
        ("2", {"Name": "TileTrack", "Track": "Center", "Shape": "Cross", "Speed": 2.0,
               "ScaleX": 2.0, "ScaleY": 2.0, "CastAnimation": "Attack1",
               "FinishAnimation": "Attack1", "ImpactSound": "SFX_Impact_Lightning_B"}),
        ("3", {"Name": "Damage", "DamageType": "Fire", "Multiplier": 1.0}),
    ]),
    (2, 90366, "Ruinous Firewalls", "Four walls of fire close in from the edges.", [
        ("0", {"Name": "OnRequest"}),
        ("1", {"Name": "Cooldown", "CD": 7000}),
        ("2", {"Name": "HitStream", "PosX": 0.0, "PosY": 6.0, "Speed": 1.0, "Duration": 15000,
               "ScaleX": 2.0, "ScaleY": 0.8, "VFX": "Ground_Fire_Attack-This_moves_VFX",
               "FinishAnimation": "Attack3", "ImpactSound": "SFX_Impact_Fire_C"}),
        ("3", {"Name": "HitStream", "PosX": 0.0, "PosY": -7.0, "Speed": 1.0, "Duration": 15000,
               "ScaleX": 2.0, "ScaleY": 0.5, "VFX": "Ground_Fire_Attack-This_moves_VFX",
               "ImpactSound": "SFX_Impact_Fire_C"}),
        ("4", {"Name": "HitStream", "PosX": -27.0, "PosY": -0.85, "Speed": 1.0, "Duration": 15000,
               "ScaleX": 0.2, "ScaleY": 2.4, "VFX": "Ground_Fire_Attack-This_moves_VFX",
               "ImpactSound": "SFX_Impact_Fire_C"}),
        ("5", {"Name": "HitStream", "PosX": 24.0, "PosY": -0.85, "Speed": 1.0, "Duration": 15000,
               "ScaleX": 0.2, "ScaleY": 2.4, "VFX": "Ground_Fire_Attack-This_moves_VFX",
               "ImpactSound": "SFX_Impact_Fire_C"}),
        ("6", {"Name": "Damage", "DamageType": "Fire", "Multiplier": 1.0}),
    ]),
    (3, 90367, "Ruinous Echoes", "Ragnafluff splits off two clones that fight at his side.", [
        ("0", {"Name": "OnRequest"}),
        ("1", {"Name": "Cooldown", "CD": 18000}),
        # server-side spawnMob (NOT a tile/MonReq): two Ragnafluff Clones (MonID 380, HP 4000,
        # lvl 5) at the captured spawn point. MaxAlive caps the board so the recurring cast can't
        # flood — it only tops the count back up to 2. Clones are real fightable adds (basic melee).
        ("2", {"Name": "Summon", "MonID": 380, "Count": 2, "MaxAlive": 2,
               "HP": 4000, "Level": 5, "X": 0.479, "Y": -11.164}),
    ]),
]


# --- bludrut bosses ---------------------------------------------------------------------
# Same pattern as Ragnafluff: each boss gets a pure skill-holder class (no rig -> not
# player-equippable) rotating its telegraphed tile skills. Every node payload below is LIFTED
# from a live-AE capture's MonReq packets for that monster, so shapes/VFX/sounds match the real
# fight. Cooldowns are the cadence-TO-next (from the capture's ReqTS gaps, rounded). DamageType is
# cosmetic (never sent to the client — only the tile node becomes a MonReq; damage is applied
# server-side as a normal hit on the gmah report); Multiplier scales that hit.
# (class_id, class_name, (mon_ids,), [(slot, skill_id, name, desc, node_list), ...])
# --- Abomilich (InfinityLichBoss, mon 429/430) ---------------------------------------------
# AE built the art and placed the monster but never gave it a fight. This is that fight,
# authored entirely in the captured tile vocabulary — the same node types Ragnafluff and the
# elementals use, so the client renders it with code AE already shipped and no mod is needed.
#
# GROUNDING, stated plainly:
#   * The node TYPES, prop names and defaults are capture-verified (docs/combat-engine
#     fixtures; the render layer replays 3837 captured nodes byte-for-byte).
#   * The monster, its art bundles and its two map placements are real AE data (mon 429 on
#     map 2239, 430 on 2241, bundles 78661 + the 78742 boss pet).
#   * The ROTATION ITSELF — which tiles, what shapes, cadence, damage — is OURS. No capture of
#     this boss fighting exists: AE's telegraphed tiles are client-rendered and reported back
#     (MonReq s2c / gmah c2s), so they never appear as monster Attack packets at all, which is
#     why monster_casts.json holds only plain autos. Tuned against Ragnafluff's captured
#     cadence (4.5s/5s/7s/18s) so it reads like an AE fight rather than guesswork.
#
# Art note: mon 429's row points at bundle 78660, which 404s on the live CDN at every version
# (78661 resolves at v1-v3), so 429 renders as nothing. seed_abomilich repoints it — see the
# guarded UPDATE below.
ABOMILICH_CLASS_ID = 9429
ABOMILICH_MON_IDS = (429, 430)
ABOMILICH_THRALL_MON = 431
ABOMILICH_LIVE_BUNDLE = 78661

_ABOMILICH_SKILLS = [
    # The lich opens graves under everyone standing still.
    (0, 94290, "Grasping Tombs", "Grave-circles open underfoot — step off the red.", [
        ("0", {"Name": "OnRequest"}),
        ("1", {"Name": "Cooldown", "CD": 4500}),
        ("2", {"Name": "HitTiles", "Shape": "Circle", "VFX": "Ground_Fire_Attack-This_moves_VFX",
               "Speed": 1.8, "ScaleX": 1.2, "ScaleY": 1.2, "CastAnimation": "Attack1",
               "FinishAnimation": "Attack1", "ImpactSound": "SFX_Impact_Fire_C"}),
        ("3", {"Name": "Damage", "DamageType": "Fire", "Multiplier": 1.0}),
    ]),
    # A wave of undeath rolls the length of the arena.
    (1, 94291, "Deathwave", "A wall of undeath sweeps the arena — jump the gap.", [
        ("0", {"Name": "OnRequest"}),
        ("1", {"Name": "Cooldown", "CD": 6000}),
        ("2", {"Name": "TileWave", "Speed": 2.2, "CastAnimation": "Attack2",
               "DuringAnimation": "Attack2", "FinishAnimation": "Attack2",
               "ImpactSound": "SFX_Impact_Lightning_B"}),
        ("3", {"Name": "Damage", "DamageType": "Fire", "Multiplier": 1.25}),
    ]),
    # Bone shrapnel scattered across the floor. Offsets pin the pattern server-side so every
    # client draws the same shards (the Ice Elemental's captured cluster does the same).
    (2, 94292, "Bone Scatter", "Shattered bone rains across the floor in a scatter.", [
        ("0", {"Name": "OnRequest"}),
        ("1", {"Name": "Cooldown", "CD": 5500}),
        ("2", {"Name": "TileCluster", "Speed": 2.4, "ScaleX": 1.1, "ScaleY": 1.1,
               "CastAnimation": "Attack3", "DuringAnimation": "Attack3",
               "FinishAnimation": "Attack3", "ImpactSound": "SFX_Impact_Earth_A",
               "ClusterOffsets": [-8.5, 1.9, 6.25, -1.4, -3.75, 2.35, 9.5, 1.15,
                                  -6.0, -1.85, 2.5, 2.1, -1.25, -1.6, 7.75, -2.25]}),
        ("3", {"Name": "Damage", "DamageType": "Fire", "Multiplier": 1.15}),
    ]),
    # The signature inverse telegraph: the ward is the ONLY safe ground.
    (3, 94293, "Soul Cage", "Only the warded ground is safe — get inside the circle.", [
        ("0", {"Name": "OnRequest"}),
        ("1", {"Name": "Cooldown", "CD": 9000}),
        ("2", {"Name": "TileSafe", "Speed": 1.4, "ScaleX": 1.6, "ScaleY": 1.6,
               "VFX": "Ground_Fire_Attack-This_moves_VFX", "CastAnimation": "Attack1",
               "DuringAnimation": "Attack1", "FinishAnimation": "Attack1",
               "ImpactSound": "SFX_Impact_Fire_C", "SafeOffsetX": 0.0, "SafeOffsetY": -2.0}),
        ("3", {"Name": "Damage", "DamageType": "Fire", "Multiplier": 1.5}),
    ]),
    # A hunting circle that follows you before it locks.
    (4, 94294, "Grave Chase", "A death-mark hunts you across the floor before it locks.", [
        ("0", {"Name": "OnRequest"}),
        ("1", {"Name": "Cooldown", "CD": 7000}),
        ("2", {"Name": "TileTrack", "Track": "Center", "Shape": "Circle", "Speed": 1.6,
               "ScaleX": 1.3, "ScaleY": 1.3, "CastAnimation": "Attack2",
               "FinishAnimation": "Attack2", "ImpactSound": "SFX_Impact_Lightning_B",
               "DelayedAnimation": "Attack2", "DelayedAnimationTime": 0.6}),
        ("3", {"Name": "Damage", "DamageType": "Fire", "Multiplier": 1.2}),
    ]),
    # Lingering miasma along both flanks — one cast, several strips (Ragnafluff's firewall shape).
    (5, 94295, "Creeping Miasma", "Corpse-fog seeps in from the flanks and lingers.", [
        ("0", {"Name": "OnRequest"}),
        ("1", {"Name": "Cooldown", "CD": 12000}),
        ("2", {"Name": "HitStream", "PosX": -22.0, "PosY": -0.85, "Speed": 1.0,
               "Duration": 12000, "ScaleX": 0.25, "ScaleY": 2.2,
               "VFX": "Ground_Fire_Attack-This_moves_VFX", "FinishAnimation": "Attack3",
               "ImpactSound": "SFX_Impact_Fire_C"}),
        ("3", {"Name": "HitStream", "PosX": 20.0, "PosY": -0.85, "Speed": 1.0,
               "Duration": 12000, "ScaleX": 0.25, "ScaleY": 2.2,
               "VFX": "Ground_Fire_Attack-This_moves_VFX",
               "ImpactSound": "SFX_Impact_Fire_C"}),
        ("4", {"Name": "Damage", "DamageType": "Fire", "Multiplier": 0.9}),
    ]),
    # Raise the dead: real fightable adds using AE's own boss-pet art (78742, live on the CDN).
    (6, 94296, "Raise Thrall", "The lich drags two thralls up out of the floor.", [
        ("0", {"Name": "OnRequest"}),
        ("1", {"Name": "Cooldown", "CD": 20000}),
        ("2", {"Name": "Summon", "MonID": ABOMILICH_THRALL_MON, "Count": 2, "MaxAlive": 2,
               "HP": 400, "Level": 5, "X": 0.0, "Y": -10.4}),
    ]),
]


def seed_abomilich(conn):
    """Seed the Abomilich fight: the skill-holder class + its seven skills, linked onto
    InfinityLichBoss (429/430). Same non-clobbering rules as the other monster classes.
    Also repoints mon 429's dead art bundle (see the module comment). Returns # linked."""
    import forge
    row = conn.execute("SELECT v FROM kv WHERE k='skill_graph_version'").fetchone()
    stored = int(row["v"]) if row and str(row["v"]).isdigit() else 0
    refresh = stored < SKILL_GRAPH_VERSION
    n = _seed_mon_class(conn, forge, ABOMILICH_CLASS_ID, "Abomilich",
                        _ABOMILICH_SKILLS, ABOMILICH_MON_IDS, refresh)
    # mon 429 shipped pointing at bundle 78660, which does not exist on the live CDN at any
    # version (probed: 78660 404s at v0-v4; 78661 serves at v1-v3), so the boss renders as an
    # invisible hitbox. Repoint it at the live twin — guarded, so a later real 78660 upload or
    # a hand-edit is never clobbered. Same self-healing shape as the greendragon fix above.
    row = conn.execute("SELECT bundle FROM monsters WHERE mon_id=?",
                       (ABOMILICH_MON_IDS[0],)).fetchone()
    if row and row["bundle"]:
        try:
            b = json.loads(row["bundle"])
        except ValueError:
            b = None
        if isinstance(b, dict) and int(b.get("ID") or 0) == 78660:
            conn.execute("UPDATE monsters SET bundle=? WHERE mon_id=?",
                         (json.dumps({"ID": ABOMILICH_LIVE_BUNDLE, "Name": "InfinityLichBoss",
                                      "Filename": "npcs/78661_infinitylichboss.unity3d",
                                      "VersionStage": 2, "VersionLive": 2},
                                     separators=(",", ":")), ABOMILICH_MON_IDS[0]))
    return n


_BOSS_CLASSES = [
    (9236, "Rock Elemental", (236,), [
        (0, 92360, "Stone Spikes", "Rock spikes erupt in a ring — step off the red.", [
            ("0", {"Name": "OnRequest"}),
            ("1", {"Name": "Cooldown", "CD": 5000}),
            ("2", {"Name": "HitTiles", "Shape": "Circle", "VFX": "Rock_Spike_Transform",
                   "Speed": 2.0, "ScaleX": 1.0, "ScaleY": 1.0, "CastAnimation": "Castcharge",
                   "FinishAnimation": "Custom", "ImpactSound": "SFX_Impact_Earth_A"}),
            ("3", {"Name": "Damage", "DamageType": "Earth", "Multiplier": 1.0}),
        ]),
        (1, 92361, "Seismic Wave", "A shockwave rolls out across the arena.", [
            ("0", {"Name": "OnRequest"}),
            ("1", {"Name": "Cooldown", "CD": 6500}),
            ("2", {"Name": "TileWave", "Speed": 2.5, "CastAnimation": "Custom0",
                   "DuringAnimation": "Custom3", "FinishAnimation": "Custom4",
                   "ImpactSound": "SFX_Impact_Earth_C"}),
            ("3", {"Name": "Damage", "DamageType": "Earth", "Multiplier": 1.25}),
        ]),
    ]),
    (9237, "Ice Elemental", (237,), [
        (0, 92370, "Ice Spikes", "Ice shards spear up in vertical rows.", [
            ("0", {"Name": "OnRequest"}),
            ("1", {"Name": "Cooldown", "CD": 5000}),
            ("2", {"Name": "HitTiles", "Shape": "VerticalRectangle", "VFX": "Ice_Spike_VFX-This_moves_VFX",
                   "Speed": 2.0, "ScaleX": 1.0, "ScaleY": 1.0, "CastAnimation": "Castcharge",
                   "FinishAnimation": "Custom", "ImpactSound": "SFX_impact_Ice_A"}),
            ("3", {"Name": "Damage", "DamageType": "Ice", "Multiplier": 1.0}),
        ]),
        (1, 92371, "Frozen Cluster", "A scattered burst of frozen shards.", [
            ("0", {"Name": "OnRequest"}),
            ("1", {"Name": "Cooldown", "CD": 5500}),
            ("2", {"Name": "TileCluster", "Speed": 2.5, "ScaleX": 1.2, "ScaleY": 1.2,
                   "CastAnimation": "Custom1", "DuringAnimation": "Custom3", "FinishAnimation": "Custom4",
                   "ImpactSound": "SFX_Impact_Ice_B",
                   "ClusterOffsets": [3.5038528, 1.3159033, -9.550458, -1.1436747, -9.759823,
                                      -1.7694695, 1.2829169, -1.1889389, 9.925665, 2.2298963,
                                      8.993893, 1.7999815, -9.230896, 1.9980936, 4.395659, 0.72934365]}),
            ("3", {"Name": "Damage", "DamageType": "Ice", "Multiplier": 1.25}),
        ]),
    ]),
    (9238, "Fire Elemental", (238,), [
        (0, 92380, "Flame Burst", "Fire erupts along the ground — don't stand in it.", [
            ("0", {"Name": "OnRequest"}),
            ("1", {"Name": "Cooldown", "CD": 5000}),
            ("2", {"Name": "HitTiles", "Shape": "Rectangle", "VFX": "Ground_Fire_Attack-This_moves_VFX",
                   "Speed": 2.0, "ScaleX": 1.0, "ScaleY": 1.0, "CastAnimation": "Castcharge",
                   "FinishAnimation": "Custom", "ImpactSound": "SFX_Impact_Fire_C"}),
            ("3", {"Name": "Damage", "DamageType": "Fire", "Multiplier": 1.0}),
        ]),
        # two walls of fire close in from top and bottom together (shared cast, like Ragnafluff's firewalls)
        (1, 92381, "Firewalls", "Two walls of fire sweep in from the edges.", [
            ("0", {"Name": "OnRequest"}),
            ("1", {"Name": "Cooldown", "CD": 5500}),
            ("2", {"Name": "HitStream", "PosX": 0.0, "PosY": 5.0, "VFX": "Ground_Fire_Attack-This_moves_VFX",
                   "Speed": 2.0, "Duration": 7000, "ScaleX": 2.0, "ScaleY": 0.5, "CastAnimation": "Custom1",
                   "DuringAnimation": "Custom2", "CompletedAnimation": "Custom3", "FinishAnimation": "Custom4",
                   "ImpactSound": "SFX_Impact_Fire_C"}),
            ("3", {"Name": "HitStream", "PosX": 0.0, "PosY": -6.0, "VFX": "Ground_Fire_Attack-This_moves_VFX",
                   "Speed": 2.0, "Duration": 7000, "ScaleX": 2.0, "ScaleY": 0.5, "CastAnimation": "Custom1",
                   "DuringAnimation": "Custom2", "CompletedAnimation": "Custom3", "FinishAnimation": "Custom4",
                   "ImpactSound": "SFX_Impact_Fire_C"}),
            ("4", {"Name": "Damage", "DamageType": "Fire", "Multiplier": 1.25}),
        ]),
    ]),
    (9239, "Evil Elemental", (239,), [
        (0, 92390, "Dark Lances", "Lances of dark lightning track and strike the center.", [
            ("0", {"Name": "OnRequest"}),
            ("1", {"Name": "Cooldown", "CD": 6500}),
            ("2", {"Name": "TileTrack", "Track": "Center", "Shape": "Rectangle",
                   "VFX": "Dark_Lighting-This_moves_VFX", "Speed": 3.0, "ScaleX": 1.0, "ScaleY": 1.0,
                   "CastAnimation": "Castcharge", "FinishAnimation": "Custom", "ImpactSound": "SFX_Impact_Ghost_A"}),
            ("3", {"Name": "Damage", "DamageType": "Darkness", "Multiplier": 1.0}),
        ]),
        (1, 92391, "Dark Nova", "A dark blast fills the room — find the safe spot.", [
            ("0", {"Name": "OnRequest"}),
            ("1", {"Name": "Cooldown", "CD": 6000}),
            ("2", {"Name": "TileSafe", "VFX": "Dark_Explosion-This_moves_VFX", "Speed": 2.25,
                   "ScaleX": 1.0, "ScaleY": 1.0, "CastAnimation": "Custom1", "DuringAnimation": "Custom2",
                   "FinishAnimation": "Custom3", "ImpactSound": "SFX_Impact_Ghost_A",
                   "DelayedAnimation": "Custom4", "DelayedAnimationTime": 1.5,
                   "SafeOffsetX": -22.042303, "SafeOffsetY": -0.7603178}),
            ("3", {"Name": "Damage", "DamageType": "Darkness", "Multiplier": 1.25}),
        ]),
    ]),
    (9278, "Groglurk", (278,), [
        (0, 92780, "Blade Sweep", "A blade sweeps toward the center — don't be caught in the line.", [
            ("0", {"Name": "OnRequest"}),
            ("1", {"Name": "Cooldown", "CD": 5500}),
            ("2", {"Name": "TileTrack", "Track": "Center", "Shape": "Rectangle", "Speed": 2.0,
                   "ScaleX": 0.75, "ScaleY": 1.5, "CastAnimation": "Castcharge",
                   "FinishAnimation": "Custom", "ImpactSound": "SFX_Whoosh_Blade_C"}),
            ("3", {"Name": "Damage", "DamageType": "Physical", "Multiplier": 1.0}),
        ]),
        (1, 92781, "Cleaving Strike", "A long axe cleave carves down the arena.", [
            ("0", {"Name": "OnRequest"}),
            ("1", {"Name": "Cooldown", "CD": 6000}),
            ("2", {"Name": "HitTiles", "Shape": "Rectangle", "Speed": 1.8, "ScaleX": 0.5, "ScaleY": 5.0,
                   "CastAnimation": "Custom1", "DuringAnimation": "Custom2", "FinishAnimation": "Custom3",
                   "ImpactSound": "SFX_Whoosh_Axe_C"}),
            ("3", {"Name": "Damage", "DamageType": "Physical", "Multiplier": 1.25}),
        ]),
        # Summon (server-side spawnMob, NOT a tile — same mechanism as Ragnafluff's Ruinous Echoes):
        # Groglurk's Mirror (MonID 308, HP/Level/spawn from the capture). One at a time.
        (2, 92782, "Mirror Image", "Groglurk conjures a mirror, then shatters it to stun you.", [
            ("0", {"Name": "OnRequest"}),
            ("1", {"Name": "Cooldown", "CD": 12000}),
            # SelfBreakMs/StunSecs: Groglurk breaks the mirror 3s after summoning it, stunning his
            # target for 3s (unless the player destroys it first, which cancels the stun).
            ("2", {"Name": "Summon", "MonID": 308, "Count": 1, "MaxAlive": 1,
                   "HP": 900, "Level": 9, "X": 14.951, "Y": -9.465,
                   "SelfBreakMs": 3000, "StunSecs": 3.0}),
        ]),
    ]),
    (9299, "IT", (299,), [
        (0, 92990, "Cross Slash", "A cross of energy scans and converges on the center.", [
            ("0", {"Name": "OnRequest"}),
            ("1", {"Name": "Cooldown", "CD": 5500}),
            ("2", {"Name": "TileTrack", "Track": "Center", "Shape": "Cross", "VFX": "Cross_VFX_This_moves_VFX",
                   "Speed": 2.0, "ScaleX": 1.0, "ScaleY": 1.0, "CastAnimation": "Custom1",
                   "FinishAnimation": "Custom3", "ImpactSound": "SFX_Impact_Flesh_B",
                   "DelayedAnimation": "Custom4", "DelayedAnimationTime": 1.5}),
            ("3", {"Name": "Damage", "DamageType": "Physical", "Multiplier": 1.0}),
        ]),
        (1, 92991, "Charge", "IT lunges across the room at high speed.", [
            ("0", {"Name": "OnRequest"}),
            ("1", {"Name": "Cooldown", "CD": 6000}),
            ("2", {"Name": "TileMove", "Speed": 20.0, "CastAnimation": "Castcharge",
                   "FinishAnimation": "Custom", "ImpactSound": "SFX_Impact_Flesh_B"}),
            ("3", {"Name": "Damage", "DamageType": "Physical", "Multiplier": 1.25}),
        ]),
    ]),
]


def _seed_mon_class(conn, forge, class_id, class_name, skills, mon_ids, refresh):
    """Seed one monster skill-holder class + its tile skills, then link its mon_ids. Shared by
    Ragnafluff and the elementals. INSERT-IF-ABSENT for class/slots/link (never clobbers a live
    SkillForge edit); each skill graph seeds when missing and refreshes once per version bump.
    Returns the number of monsters newly linked."""
    conn.execute(
        "INSERT INTO classes(class_id, name, bundle, rig, resource) VALUES(?,?,?,?,?) "
        "ON CONFLICT(class_id) DO NOTHING",
        (class_id, class_name, "", None, None))
    for slot, skill_id, name, desc, node_list in skills:
        data, forge_data = forge.linear_graph(node_list)
        srow = conn.execute("SELECT data FROM skills WHERE skill_id=?", (skill_id,)).fetchone()
        cur = (srow["data"] or "").replace(" ", "") if srow else ""
        empty = (srow is None) or (not cur) or cur in ("[{},{}]", "[]", "null")
        if empty or refresh:
            conn.execute(
                "INSERT INTO skills(skill_id, action, name, description, icon, slot, data, forge_data) "
                "VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(skill_id) DO UPDATE SET "
                "name=excluded.name, description=excluded.description, "
                "data=excluded.data, forge_data=excluded.forge_data",
                (skill_id, 0, name, desc, "", slot,
                 json.dumps(data, separators=(",", ":")),
                 json.dumps(forge_data, separators=(",", ":"))))
        conn.execute(
            "INSERT INTO class_skills(class_id, slot, skill_id) VALUES(?,?,?) "
            "ON CONFLICT(class_id, slot) DO NOTHING",
            (class_id, slot, skill_id))
    # Self-healing link: only when the monster has NO working tile skill (see Ragnafluff note).
    n = 0
    for mid in mon_ids:
        cur_row = conn.execute("SELECT class_id FROM monsters WHERE mon_id=?", (mid,)).fetchone()
        if cur_row is None:
            continue
        if cur_row["class_id"] == class_id or forge.monster_skills(conn, mid):
            continue
        conn.execute("UPDATE monsters SET class_id=? WHERE mon_id=?", (class_id, mid))
        n += 1
    return n


def seed_monster_skills(conn):
    """Give the boss monsters (Ragnafluff + the bludrut elementals) a class + their captured tile
    skills so monster skills work end-to-end. INSERT-IF-ABSENT for the class/slots/link (never
    clobbers a live SkillForge edit); the skill graphs follow the same empty/refresh gate as
    seed_skill_graphs. Run BEFORE seed_skill_graphs (it owns writing the version kv). Returns the
    number of monsters linked."""
    import forge
    row = conn.execute("SELECT v FROM kv WHERE k='skill_graph_version'").fetchone()
    stored = int(row["v"]) if row and str(row["v"]).isdigit() else 0
    refresh = stored < SKILL_GRAPH_VERSION

    n = _seed_mon_class(conn, forge, RAGNAFLUFF_CLASS_ID, "Ragnafluff The Ruinous",
                        [(s, sid, nm, d, nl) for s, sid, nm, d, nl in _RAGNAFLUFF_SKILLS],
                        RAGNAFLUFF_MON_IDS, refresh)
    for class_id, class_name, mon_ids, skills in _BOSS_CLASSES:
        n += _seed_mon_class(conn, forge, class_id, class_name, skills, mon_ids, refresh)
    n += seed_abomilich(conn)               # InfinityLichBoss (429/430) tile fight
    # The greendragon boss must stay Hostile (attackable). greendragon.json has reactionType:1,
    # but the monster row predated that and the seed is INSERT-IF-ABSENT, so reaction_type was
    # NULL -> the authored/compiled monBranch served it as a neutral click-to-talk NPC. Backfill
    # it (only when not already Hostile) so a reseed can't re-break the boss.
    conn.execute("UPDATE monsters SET reaction_type=1 "
                 "WHERE mon_id=364 AND (reaction_type IS NULL OR reaction_type<>1)")
    return n


# --- Paladin (Reduxidain, class 69420) -----------------------------------------------------
# OUR original class, designed with Redux: a stacking "Conviction" resource (combat.py owns the
# model — build on auto/Vow, per-stack empowerment, Smite consumes the pool, idle decay). The
# class/skills skeleton was first authored live in the SkillForge; this seeds the canonical
# graphs + rig + resource so a fresh DB gets the whole class, refreshed once per version bump
# (same non-clobbering rule as the DS graphs).
PALADIN_CLASS_ID = 69420
# v2: Vow + Smite made single-shot (dropped the Range input node) so they resolve in one atomic
#     Attack like Protection/Guard/the auto — the igai/gai Range handshake could abort mid-cast,
#     spending Smite's pool server-side while the Damage/Resource nodes never sent (looked like
#     "Smite didn't consume / missed"). Smite's Damage is now Guaranteed (skips the to-hit roll)
#     so the pool-dump always lands.
# v3: auto Conviction gain 1 -> 3 (CONV_AUTO_GAIN) — +1/2s was imperceptible, felt like autos
#     weren't building at all.
# v4: InfinityHero VFX pass — the rig's ClassParticleBundle switches from the borrowed Warrior
#     bundle to the unshipped classInfinityHero_Default (78541, live on AE's CDN, never
#     referenced by any shipped class), and each skill cues its classInfinityHero_S<slot>_P<n>
#     particles. Protection/Guard gain cast animations (Castgood/Cast2 — unused generic states
#     in the shared rig) because the client only spawns a Particle node when its Animation cue
#     actually plays on the caster. Icons: the unused shipped ClassSkillIcons (opportunitystrike
#     ×2, Healer/gears, Dragonslayer/spin + downstrike). seed_paladin also swaps the particle
#     bundle on live rigs still pointing at the Warrior one (78047).
# v5: reconstructs the leaked InfinityHero slot-3 Meteor using all six S3 emitters. The
#     existing support/offense buttons establish Healer/Warrior Aspect so Meteor can execute
#     both tooltip branches without inventing the rest of the unrevealed official kit.
# v6: uses the actual classInfinityHero_S1_P4 composite for Meteor's victim-side finisher.
#     That prefab owns InfinitySword-Animation plus the giant sword, three lightning strikes,
#     gold pillars, runes, smoke, and explosion; a 6s lifetime lets its full sequence finish.
# v7: writes the combat-engine RULE CONFIG (PALADIN_RULES) into classes.raw — the Conviction
#     mechanic expressed as data (combat_engine/rules.py). The graphs are unchanged; the
#     Python special-cases in combat.py still drive live combat until the cutover
#     (test_port_parity.py proves the two paths emit identical Attacks).
PALADIN_GRAPH_VERSION = 7

_PALADIN_RESOURCE = {"model": "conviction", "ResourceColor": 16764498, "MaxRP": 50}
# The rig carries the ReduxPaladin class-armor item id (69420) — forge.class_for_armor_item
# reads rig.ID, so equipping that armor switches to this class. Skin still borrows the Warrior
# armor; particles come from AE's unshipped InfinityHero kit (its _Default prefab also carries
# classWarrior_concurrent_Attack1_Auto/2/3 clips, so it drives the Warrior body states the
# Paladin uses — a drop-in upgrade, no client patch).
_PALADIN_RIG = {
    "ID": 69420,
    "Bundle": {"ID": 15775, "Name": "Warrior", "Filename": "armors/15775_NewWarriorB2.unity3d",
               "VersionStage": 3, "VersionLive": 3},
    "ClassParticleBundle": {"ID": 78541, "Name": "classInfinityHero_Default",
                            "Filename": "gameassets/classes/78541_classinfinityhero_default.unity3d",
                            "VersionStage": 1, "VersionLive": 1},
    "PrefabName": "ArmorSlots", "EquipSpot": 6, "ItemType": 21,
}

# slot -> (skill_id, name, icon, description, node_list). Graph mechanics live in combat.py
# (CONV_* / CONVICTION_SCALING / LIFELINK / the "Paladin's Guard" aura); the multipliers here
# are each skill's BASE, before Conviction scaling. Icons are the shipped-but-unused entries in
# Resources/UI/SpriteAssets/ClassSkillIcons. Particles are the unshipped InfinityHero kit
# (classInfinityHero_S<slot+1>_P<n>, see _PALADIN_RIG); each Particle node must cue an
# Animation the caster plays in the same cast or the client never spawns it. Meteor's leaked
# sword/lightning finisher is unusually stored as S1_P4 despite being used by the slot-3 skill.
_PALADIN_SKILLS = [
    (0, 90373, "Auto Attack", "Warrior/opportunitystrike",
     "Attack your enemy, applying a stack of Conviction. Conviction stacks up to 50, "
     "empowering each of your abilities per stack. Out of combat, Conviction fades "
     "after 10 seconds.", [
        ("0", {"Name": "OnRequest"}),
        ("1", {"Name": "Cooldown", "CD": 2000}),
        ("2", {"Name": "PlayerAnimation", "Animation": "Attack1_Auto,Attack2,Attack3"}),
        ("3", {"Name": "Damage", "DamageType": "Physical", "Multiplier": 1.0}),
        ("4", {"Name": "Particle", "Particle": "classInfinityHero_S1_P1",
               "Animation": "Attack1_Auto", "X": -2, "Y": 3}),
        ("5", {"Name": "Particle", "Particle": "classInfinityHero_S1_P2",
               "Animation": "Attack2", "X": -1, "Y": 3}),
        ("6", {"Name": "Particle", "Particle": "classInfinityHero_S1_P3",
               "Animation": "Attack3", "X": 0, "Y": 3}),
        ("7", {"Name": "Resource", "Amount": 3}),
        ("8", {"Name": "UpdateAnimation", "Value": "2H_Fight"}),
    ]),
    (1, 90369, "Paladin's Vow", "Rogue/opportunitystrike",
     "Attack your enemy, applying two stacks of Conviction. Deals an additional 3% Physical "
     "Damage per stack of Conviction, up to 150% Bonus Damage.", [
        ("0", {"Name": "OnRequest"}),
        ("1", {"Name": "Cooldown", "CD": 2000}),
        ("2", {"Name": "PlayerAnimation", "Animation": "Attack1"}),
        ("3", {"Name": "Damage", "DamageType": "Physical", "Multiplier": 1.2}),
        ("4", {"Name": "Particle", "Particle": "classInfinityHero_S2_P1",
               "Animation": "Attack1", "X": 0, "Y": 3}),
        ("5", {"Name": "Particle", "Particle": "classInfinityHero_S2_P2",
               "Animation": "Attack1", "X": 2, "Y": 3}),
        ("6", {"Name": "Particle", "Particle": "classInfinityHero_S2_P3",
               "Animation": "Attack1", "X": 4, "Y": 3}),
        ("7", {"Name": "Resource", "Amount": 2}),
        ("8", {"Name": "UpdateAnimation", "Value": "2H_Fight"}),
    ]),
    (2, 90370, "Meteor", "Mage/fireball",
     "Rains down meteors that deal magical damage based on INT and WIS to up to 4 targets. "
     "Warrior Aspect: hits 1 target for 50% increased damage and leaves a burning field for "
     "5 seconds. Healer Aspect: applies Suppression, reducing Crit Chance, Physical Damage, "
     "and Magical Damage by 10% for 6 seconds. 4 second cooldown.", [
        ("0", {"Name": "OnRequest"}),
        ("1", {"Name": "Cooldown", "CD": 4000}),
        ("2", {"Name": "PlayerAnimation", "Animation": "Attack1"}),
        ("3", {"Name": "AllEnemies"}),
        ("4", {"Name": "Damage", "DamageType": "Magical", "Multiplier": 1.0,
               "MaxTargets": 4, "Targets": {"id": "3"}}),
        ("5", {"Name": "Particle", "Particle": "classInfinityHero_S3_P1",
               "Animation": "Attack1", "Time": 0, "X": 0, "Y": 0,
               "Lifetime": 5000}),
        ("6", {"Name": "Particle", "Particle": "classInfinityHero_S3_P2",
               "Animation": "Attack1", "Time": 0, "X": 0, "Y": 0,
               "Lifetime": 5000}),
        ("7", {"Name": "Target"}),
        ("8", {"Name": "Particle", "Particle": "classInfinityHero_S1_P4",
               "Animation": "Attack1", "Time": 0, "X": 0, "Y": 0,
               "Lifetime": 6000, "Targets": {"id": "7"}}),
        ("9", {"Name": "Resource", "Amount": 0}),
        ("10", {"Name": "UpdateAnimation", "Value": "2H_Fight"}),
    ]),
    (3, 90371, "Healer Aspect", "Healer/heal",
     "Assume the Healer Aspect and bless yourself and up to 5 allies for 6 seconds, increasing "
     "damage dealt and reducing incoming damage by 25%. While active, Meteor hits up to 4 "
     "targets and applies Suppression. 4 second cooldown.", [
        ("0", {"Name": "OnRequest"}),
        ("1", {"Name": "Cooldown", "CD": 4000}),
        ("2", {"Name": "PlayerAnimation", "Animation": "Cast2"}),
        ("3", {"Name": "Aura", "AuraName": "Paladin's Guard", "Duration": 6, "MaxTargets": 6}),
        ("4", {"Name": "Particle", "Particle": "classInfinityHero_S4_P1",
               "Animation": "Cast2", "X": 0, "Y": 0}),
        ("5", {"Name": "Particle", "Particle": "classInfinityHero_S4_P2",
               "Animation": "Cast2", "X": 0, "Y": 2}),
        ("6", {"Name": "Particle", "Particle": "classInfinityHero_S4_P3",
               "Animation": "Cast2", "X": 0, "Y": 4}),
        ("7", {"Name": "Resource", "Amount": 0}),
        ("8", {"Name": "UpdateAnimation", "Value": "2H_Fight"}),
    ]),
    (4, 90372, "Warrior Aspect", "Rogue/viper",
     "Assume the Warrior Aspect and smite your opponent. Consumes all stacks of Conviction to "
     "deal 5% bonus Magical Damage per stack and heals your party for 30% of damage dealt. "
     "While active, Meteor hits 1 target for 50% increased damage and leaves a burning field.", [
        ("0", {"Name": "OnRequest"}),
        ("1", {"Name": "Cooldown", "CD": 6000}),
        ("2", {"Name": "PlayerAnimation", "Animation": "Attack3"}),
        ("3", {"Name": "Damage", "DamageType": "Magical", "Multiplier": 1.5, "Guaranteed": True}),
        ("4", {"Name": "Particle", "Particle": "classInfinityHero_S5_P1",
               "Animation": "Attack3", "X": 2, "Y": 6}),
        ("5", {"Name": "Particle", "Particle": "classInfinityHero_S5_P2",
               "Animation": "Attack3", "X": 2, "Y": 3}),
        ("6", {"Name": "Particle", "Particle": "classInfinityHero_S5_P3",
               "Animation": "Attack3", "X": 2, "Y": 0}),
        ("7", {"Name": "Resource", "Amount": 0}),
        ("8", {"Name": "UpdateAnimation", "Value": "2H_Fight"}),
    ]),
]


# --- Conviction as DATA (combat_engine rule config, stage-4 port) --------------------------
# The exact mechanics combat.py hardcodes for class 69420, re-expressed in the rule-graph
# authoring format (combat_engine/rules.py docstring): stacks snapshot BEFORE the gain (a
# cast scales on the stacks you HAD), builders +3 auto/+2 Vow, Smite consumes all for
# +5%/stack and a 30% party lifelink, the Meteor aspect branch, guard auras on allies.
# Stored in classes.raw["rules"]; live combat still runs the Python path until cutover.
_PALADIN_ASPECT_GROUP = ["warrior", "healer"]
PALADIN_RULES = {
    "engine": 1,
    "resource": {"model": "conviction", "max": 50},
    "skills": {
        "90373": [                                          # Auto: +3 Conviction
            {"Do": "Formula", "Var": "stacks", "Expr": "rp"},
            {"Do": "ResourceOp", "Op": "gain", "Amount": 3},
            {"Do": "Graph"},
        ],
        "90369": [                                          # Vow: +2, +3%/stack
            {"Do": "Formula", "Var": "stacks", "Expr": "rp"},
            {"Do": "ResourceOp", "Op": "gain", "Amount": 2},
            {"Do": "Graph",
             "Overlay": {"Damage": {"MultScale": {"$": "1 + 0.03*stacks"}}}},
        ],
        "90370": [                                          # Meteor: aspect branch
            {"Do": "Branch", "On": "aspect",
             "Cases": {"healer": [
                 {"Do": "Graph", "Overlay": {"Damage": {"MaxTargets": 4}}},
                 # uniquenessType pinned to combat.py's legacy 1 (the REAL
                 # InfinityHero capture uses 0; the registry default follows it)
                 {"Do": "ApplyAura", "Aura": "Suppression",
                  "Targets": "@hits", "MaxTargets": 4, "uniquenessType": 1},
             ]},
             "Default": [                                   # warrior (the default aspect)
                 {"Do": "Graph", "Overlay": {"Damage": {"Targets": None,
                                                        "MaxTargets": 1,
                                                        "MultScale": 1.5}}},
                 {"Do": "ApplyAura", "Aura": "Burning Field",
                  "Targets": "@hits", "MaxTargets": 1},
             ]},
        ],
        "90371": [                                          # Healer Aspect: party guard
            {"Do": "SetAspect", "Aspect": "healer", "Group": _PALADIN_ASPECT_GROUP},
            {"Do": "Graph", "Overlay": {"Aura": {"Targets": "@allies"}}},
        ],
        "90372": [                                          # Smite: spend all + lifelink
            {"Do": "SetAspect", "Aspect": "warrior", "Group": _PALADIN_ASPECT_GROUP},
            {"Do": "ResourceOp", "Op": "spend_all"},
            {"Do": "Graph",
             "Overlay": {"Damage": {"MultScale": {"$": "1 + 0.05*spent"}}}},
            {"Do": "Branch", "If": "dmg_total > 0", "Then": [
                {"Do": "Heal", "Amount": {"$": "max(1, round(dmg_total*0.30))"},
                 "Targets": "@allies", "MaxTargets": 6, "Immediate": True}]},
        ],
    },
}


def _seed_class_rules(conn, class_id, rules):
    """Write a class's combat-engine rule config into classes.raw["rules"]
    (merging over any other raw keys). Caller gates on its refresh flag."""
    crow = conn.execute("SELECT raw FROM classes WHERE class_id=?", (class_id,)).fetchone()
    try:
        raw = json.loads(crow["raw"]) if crow and crow["raw"] else {}
    except ValueError:
        raw = {}
    if not isinstance(raw, dict):
        raw = {}
    raw["rules"] = rules
    conn.execute("UPDATE classes SET raw=? WHERE class_id=?",
                 (json.dumps(raw, separators=(",", ":")), class_id))


def seed_paladin(conn):
    """Seed the Paladin class row (resource + rig), its five skill graphs and slot links.
    Same rules as the other canonical graphs: INSERT-IF-ABSENT for the class/slots, graphs
    seed when missing and refresh once per PALADIN_GRAPH_VERSION bump — a live SkillForge
    edit on an up-to-date DB is never clobbered. Returns # of skills written."""
    import forge
    row = conn.execute("SELECT v FROM kv WHERE k='paladin_graph_version'").fetchone()
    stored = int(row["v"]) if row and str(row["v"]).isdigit() else 0
    refresh = stored < PALADIN_GRAPH_VERSION

    rig_json = json.dumps(_PALADIN_RIG, separators=(",", ":"))
    conn.execute(
        "INSERT INTO classes(class_id, name, bundle, rig, resource) VALUES(?,?,?,?,?) "
        "ON CONFLICT(class_id) DO NOTHING",
        (PALADIN_CLASS_ID, "Reduxidain", "", rig_json,
         json.dumps(_PALADIN_RESOURCE, separators=(",", ":"))))
    # the live row predates the rig — backfill it (only while missing, so a later custom-art
    # rig survives reseeds) to make equipping the ReduxPaladin armor switch to this class
    conn.execute("UPDATE classes SET rig=? WHERE class_id=? AND (rig IS NULL OR rig='')",
                 (rig_json, PALADIN_CLASS_ID))
    # v4 particle-bundle migration: existing rigs that still borrow the Warrior particles get
    # the InfinityHero bundle swapped in surgically (only that key, so a custom armor Bundle
    # survives); a rig already pointing anywhere else is custom art and is left alone.
    if refresh:
        crow = conn.execute("SELECT rig FROM classes WHERE class_id=?",
                            (PALADIN_CLASS_ID,)).fetchone()
        try:
            live_rig = json.loads(crow["rig"]) if crow and crow["rig"] else None
        except ValueError:
            live_rig = None
        if live_rig and (live_rig.get("ClassParticleBundle") or {}).get("ID") == 78047:
            live_rig["ClassParticleBundle"] = _PALADIN_RIG["ClassParticleBundle"]
            conn.execute("UPDATE classes SET rig=? WHERE class_id=?",
                         (json.dumps(live_rig, separators=(",", ":")), PALADIN_CLASS_ID))

    n = 0
    for slot, skill_id, name, icon, desc, node_list in _PALADIN_SKILLS:
        data, forge_data = forge.linear_graph(node_list)
        srow = conn.execute("SELECT data FROM skills WHERE skill_id=?", (skill_id,)).fetchone()
        cur = (srow["data"] or "").replace(" ", "") if srow else ""
        empty = (srow is None) or (not cur) or cur in ("[{},{}]", "[]", "null")
        if empty or refresh:
            conn.execute(
                "INSERT INTO skills(skill_id, action, name, description, icon, slot, data, forge_data) "
                "VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(skill_id) DO UPDATE SET "
                "name=excluded.name, description=excluded.description, icon=excluded.icon, "
                "data=excluded.data, forge_data=excluded.forge_data",
                (skill_id, 1 if slot == 0 else 0, name, desc, icon, slot,
                 json.dumps(data, separators=(",", ":")),
                 json.dumps(forge_data, separators=(",", ":"))))
            n += 1
        conn.execute(
            "INSERT INTO class_skills(class_id, slot, skill_id) VALUES(?,?,?) "
            "ON CONFLICT(class_id, slot) DO NOTHING",
            (PALADIN_CLASS_ID, slot, skill_id))
    if refresh:
        _seed_class_rules(conn, PALADIN_CLASS_ID, PALADIN_RULES)    # v7: Conviction as data
    conn.execute("INSERT INTO kv(k,v) VALUES('paladin_graph_version',?) "
                 "ON CONFLICT(k) DO UPDATE SET v=excluded.v", (str(PALADIN_GRAPH_VERSION),))
    return n


# --- Voidwalker (class 2064) ----------------------------------------------------------------
# OUR original class, built entirely from content AE shipped but never used: the Void moveset
# hidden in the shared Characters rig (ClassVoid-*/VOIDClass-* animator states, incl. a combat
# idle and a hold-pose "domain"), the Infinite Legion Dark Caster armor (item 47465 — already a
# class item in the catalog, MetaString "2064" = AE's never-implemented Legion Evolved Dark
# Caster class, so we claim that id), and the Mage particle bundle for VFX (arcane smoke + the
# classMage_MageShield _Appear/_Exit aura pair that powers Event Horizon's bubble).
#
# Resource: "Hunger" — the same stacking-pool machinery as Paladin's Conviction (combat.py
# CONV_*: auto +3, Siphon +2, Maw +5, Manifest consumes all; idle decay applies — the Void's
# hunger fades when unfed). Purple bar. LIFELINK feeds Siphon; AURA_FX carries Umbral Rot (DoT)
# and Event Horizon (guard kind: -20% incoming, outgoing scales on Hunger at cast).
#
# Animation notes: every state used here has a clean exit transition AND the OnAnimationExit
# StateMachineBehaviour (required for cued particles to fire). ClassVoid-DomainIdle is a hold
# state Event Horizon sets AS the combat idle on purpose: you sink into the domain and stay
# there until your next cast's UpdateAnimation pulls you back out. The SummonGo-Blob/Conduit
# hold states carry no behaviour and are unusable for cued VFX — avoided.
# v2: Hungering Maw's cast state ClassVoid-SummonGo-Blob -> ClassVoid-SummonReturn. Cued
#     particles fire from the OnAnimationExit StateMachineBehaviour attached per-state in the
#     Player controller; SummonGo-Blob (a designed hold with no exit transition) carries NO
#     behaviour, so its particle cues could never fire and the pose could stick. SummonReturn
#     has the behaviour + a clean exit to VOIDClass-Idle. (The companion server fix: equip_item
#     now serves the class RIG as the eqp.Class entry — the stripped catalog item carries no
#     ClassParticleBundle, which nulled Player.classBundle and killed ALL class VFX until relog.)
# v3: the ult is now the AUTHENTIC Umbra showpiece — "Lethal Abomination" (name from the tech
#     demo footage): the all-stacks nuke also MonTransforms you into the Creeping Shadow
#     monster (bundle 66126, the "IT" red-eyed shadow mass) for 8s of Shadow Form (+25% dmg,
#     -15% incoming; combat.py aura expiry sends the detransform automatically). The client's
#     NodeMonTransform replicates the morph to the whole area.
# v4: writes the combat-engine RULE CONFIG (VOID_RULES) into classes.raw — Hunger as data,
#     same port pattern as PALADIN_RULES (graphs untouched, Python path still live).
VOID_CLASS_ID = 2064
VOID_ARMOR_ITEM = 47465
VOID_GRAPH_VERSION = 4

# --- Hunger as DATA (stage-4 port): the same stacking machinery, Void-flavored — builders
# --- +3 Rend/+2 Siphon/+5 Maw, Siphon drinks back 35%, Manifest consumes all for +5%/stack.
VOID_RULES = {
    "engine": 1,
    "resource": {"model": "conviction", "max": 50},
    "skills": {
        "90380": [                                          # Void Rend: +3 Hunger
            {"Do": "Formula", "Var": "stacks", "Expr": "rp"},
            {"Do": "ResourceOp", "Op": "gain", "Amount": 3},
            {"Do": "Graph"},
        ],
        "90381": [                                          # Essence Siphon: +2, +2%/stack,
            {"Do": "Formula", "Var": "stacks", "Expr": "rp"},   # 35% party lifelink
            {"Do": "ResourceOp", "Op": "gain", "Amount": 2},
            {"Do": "Graph",
             "Overlay": {"Damage": {"MultScale": {"$": "1 + 0.02*stacks"}}}},
            {"Do": "Branch", "If": "dmg_total > 0", "Then": [
                {"Do": "Heal", "Amount": {"$": "max(1, round(dmg_total*0.35))"},
                 "Targets": "@allies", "MaxTargets": 6, "Immediate": True}]},
        ],
        "90382": [                                          # Hungering Maw: +5, +2%/stack,
            {"Do": "Formula", "Var": "stacks", "Expr": "rp"},   # Umbral Rot on the victim
            {"Do": "ResourceOp", "Op": "gain", "Amount": 5},
            {"Do": "Graph",
             "Overlay": {"Damage": {"MultScale": {"$": "1 + 0.02*stacks"}},
                         "Aura": {"Targets": "@target"}}},
        ],
        "90383": [                                          # Event Horizon: party guard
            {"Do": "Graph", "Overlay": {"Aura": {"Targets": "@allies"}}},
        ],
        "90384": [                                          # Lethal Abomination: spend all,
            {"Do": "ResourceOp", "Op": "spend_all"},        # +5%/stack, Shadow Form morph
            {"Do": "Graph",
             "Overlay": {"Damage": {"MultScale": {"$": "1 + 0.05*spent"}},
                         "Aura": {"Targets": "@allies"}}},
        ],
    },
}

_VOID_RESOURCE = {"model": "conviction", "ResourceColor": 10170623, "MaxRP": 50}
_VOID_RIG = {
    "ID": VOID_ARMOR_ITEM,
    "Bundle": {"ID": 31232, "Name": "Infinite Legion Dark Caster",
               "Filename": "armors/31232_2019EvoDarkCasterr1.unity3d",
               "VersionStage": 4, "VersionLive": 4},
    "ClassParticleBundle": {"ID": 78048, "Name": "classMage_Default",
                            "Filename": "gameassets/classes/78048_classmage_default.unity3d",
                            "VersionStage": 2, "VersionLive": 2},
    "PrefabName": "ArmorSlots", "EquipSpot": 6, "ItemType": 21,
}

# slot -> (skill_id, name, icon, description, node_list). Icons are shipped ClassSkillIcons
# picked for theme (the class has no art of its own). Numbers (multipliers/gains) are BASE,
# before Hunger scaling in combat.py.
_VOID_SKILLS = [
    (0, 90380, "Void Rend", "Rogue/backstab",
     "Rake your enemy with claws of living void, feeding you 3 Hunger. Hunger stacks to 50, "
     "empowering your abilities per stack, and fades when you go unfed for 10 seconds.", [
        ("0", {"Name": "OnRequest"}),
        ("1", {"Name": "Cooldown", "CD": 2000}),
        ("2", {"Name": "PlayerAnimation", "Animation": "ClassVoid-Claw"}),
        ("3", {"Name": "Damage", "DamageType": "Physical", "Multiplier": 1.0}),
        ("4", {"Name": "Particle", "Particle": "classMage_S1_P1",
               "Animation": "ClassVoid-Claw", "X": 1, "Y": 2}),
        ("5", {"Name": "Resource", "Amount": 3}),
        ("6", {"Name": "UpdateAnimation", "Value": "VOIDClass-Idle"}),
    ]),
    (1, 90381, "Essence Siphon", "Rogue/viper",
     "Drain your target's essence, dealing magical damage empowered 2% per stack of Hunger "
     "and feeding you 2 Hunger. You and your party drink back 35% of the damage dealt as "
     "healing.", [
        ("0", {"Name": "OnRequest"}),
        ("1", {"Name": "Cooldown", "CD": 3000}),
        ("2", {"Name": "PlayerAnimation", "Animation": "ClassVoid-Siphon"}),
        ("3", {"Name": "Damage", "DamageType": "Magical", "Multiplier": 1.1}),
        ("4", {"Name": "Particle", "Particle": "classMage_S3_P1",
               "Animation": "ClassVoid-Siphon", "X": 2, "Y": 2}),
        ("5", {"Name": "Particle", "Particle": "classMage_S3_P2",
               "Animation": "ClassVoid-Siphon", "X": 0, "Y": 2}),
        ("6", {"Name": "Resource", "Amount": 2}),
        ("7", {"Name": "UpdateAnimation", "Value": "VOIDClass-Idle"}),
    ]),
    (2, 90382, "Hungering Maw", "Rogue/throw",
     "Tear off a piece of the void and hurl it at your target. Deals magical damage empowered "
     "2% per stack of Hunger and leaves Umbral Rot gnawing at them, a 5 second damage over "
     "time. The act of feeding grants 5 Hunger.", [
        ("0", {"Name": "OnRequest"}),
        ("1", {"Name": "Cooldown", "CD": 6000}),
        ("2", {"Name": "PlayerAnimation", "Animation": "ClassVoid-SummonReturn"}),
        ("3", {"Name": "Damage", "DamageType": "Magical", "Multiplier": 1.15}),
        ("4", {"Name": "Aura", "AuraName": "Umbral Rot", "Duration": 5}),
        ("5", {"Name": "Particle", "Particle": "classMage_S2_P1",
               "Animation": "ClassVoid-SummonReturn", "X": 2, "Y": 3}),
        ("6", {"Name": "Particle", "Particle": "classMage_S2_P2",
               "Animation": "ClassVoid-SummonReturn", "X": 4, "Y": 2}),
        ("7", {"Name": "Resource", "Amount": 0}),
        ("8", {"Name": "UpdateAnimation", "Value": "VOIDClass-Idle"}),
    ]),
    (3, 90383, "Event Horizon", "Mage/shield",
     "Sink into your domain, blessing yourself and up to 5 allies for 6 seconds: incoming "
     "damage is reduced 20% and damage dealt is empowered by your Hunger. You remain within "
     "the domain until you next act.", [
        ("0", {"Name": "OnRequest"}),
        ("1", {"Name": "Cooldown", "CD": 12000}),
        ("2", {"Name": "PlayerAnimation", "Animation": "VOIDClass-DomainIntro"}),
        ("3", {"Name": "Aura", "AuraName": "Event Horizon", "Duration": 6, "MaxTargets": 6}),
        ("4", {"Name": "AuraVFX", "AuraName": "Event Horizon", "VFX": "classMage_MageShield"}),
        ("5", {"Name": "Particle", "Particle": "classMage_S5_P3",
               "Animation": "VOIDClass-DomainIntro", "X": 0, "Y": 0}),
        ("6", {"Name": "Resource", "Amount": 0}),
        ("7", {"Name": "UpdateAnimation", "Value": "ClassVoid-DomainIdle"}),
    ]),
    (4, 90384, "Lethal Abomination", "Mage/explosion",
     "Give the void your form. Consumes ALL stacks of Hunger to deal massive magical damage "
     "(+5% per stack consumed, never misses) and become a Lethal Abomination for 8 seconds: "
     "a creature of living shadow that deals 25% more damage and takes 15% less.", [
        ("0", {"Name": "OnRequest"}),
        ("1", {"Name": "Cooldown", "CD": 20000}),
        ("2", {"Name": "PlayerAnimation", "Animation": "VOIDClass-Transform"}),
        ("3", {"Name": "Damage", "DamageType": "Magical", "Multiplier": 1.5, "Guaranteed": True}),
        ("4", {"Name": "MonTransform", "Linkage": "monster-CreepingShadow", "Scale": 1.5,
               "Bundle": {"ID": 66126, "Name": "creepingshadow",
                          "Filename": "npcs/66126_creepingshadow.unity3d",
                          "VersionStage": 5, "VersionLive": 5}}),
        ("5", {"Name": "Aura", "AuraName": "Shadow Form", "Duration": 8, "MaxTargets": 1}),
        ("6", {"Name": "Particle", "Particle": "classMage_S4_P1",
               "Animation": "VOIDClass-Transform", "X": 1, "Y": 2}),
        ("7", {"Name": "Particle", "Particle": "classMage_S4_P2",
               "Animation": "VOIDClass-Transform", "X": 2, "Y": 4}),
        ("8", {"Name": "Resource", "Amount": 0}),
        ("9", {"Name": "UpdateAnimation", "Value": "VOIDClass-Idle"}),
    ]),
]


def seed_void(conn):
    """Seed the Voidwalker class row (resource + rig), its five skill graphs and slot links.
    Same rules as seed_paladin: INSERT-IF-ABSENT for the class/slots, graphs seed when missing
    and refresh once per VOID_GRAPH_VERSION bump — live SkillForge edits on an up-to-date DB
    are never clobbered. The armor item (47465) already exists in the catalog; no item seeding
    needed. Returns # of skills written."""
    import forge
    row = conn.execute("SELECT v FROM kv WHERE k='void_graph_version'").fetchone()
    stored = int(row["v"]) if row and str(row["v"]).isdigit() else 0
    refresh = stored < VOID_GRAPH_VERSION

    rig_json = json.dumps(_VOID_RIG, separators=(",", ":"))
    conn.execute(
        "INSERT INTO classes(class_id, name, bundle, rig, resource) VALUES(?,?,?,?,?) "
        "ON CONFLICT(class_id) DO NOTHING",
        (VOID_CLASS_ID, "Voidwalker", "", rig_json,
         json.dumps(_VOID_RESOURCE, separators=(",", ":"))))
    conn.execute("UPDATE classes SET rig=? WHERE class_id=? AND (rig IS NULL OR rig='')",
                 (rig_json, VOID_CLASS_ID))

    n = 0
    for slot, skill_id, name, icon, desc, node_list in _VOID_SKILLS:
        data, forge_data = forge.linear_graph(node_list)
        srow = conn.execute("SELECT data FROM skills WHERE skill_id=?", (skill_id,)).fetchone()
        cur = (srow["data"] or "").replace(" ", "") if srow else ""
        empty = (srow is None) or (not cur) or cur in ("[{},{}]", "[]", "null")
        if empty or refresh:
            conn.execute(
                "INSERT INTO skills(skill_id, action, name, description, icon, slot, data, forge_data) "
                "VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(skill_id) DO UPDATE SET "
                "name=excluded.name, description=excluded.description, icon=excluded.icon, "
                "data=excluded.data, forge_data=excluded.forge_data",
                (skill_id, 1 if slot == 0 else 0, name, desc, icon, slot,
                 json.dumps(data, separators=(",", ":")),
                 json.dumps(forge_data, separators=(",", ":"))))
            n += 1
        conn.execute(
            "INSERT INTO class_skills(class_id, slot, skill_id) VALUES(?,?,?) "
            "ON CONFLICT(class_id, slot) DO NOTHING",
            (VOID_CLASS_ID, slot, skill_id))
    if refresh:
        _seed_class_rules(conn, VOID_CLASS_ID, VOID_RULES)          # v4: Hunger as data
    conn.execute("INSERT INTO kv(k,v) VALUES('void_graph_version',?) "
                 "ON CONFLICT(k) DO UPDATE SET v=excluded.v", (str(VOID_GRAPH_VERSION),))
    return n


# --- Infinity Hero (class 2022) — AE's real hero class, rebuilt as PURE DATA ----------------
# Every mechanic below is decoded from 447 captured AE casts of the actual class (233 in
# fixtures/infinity_hero_casts.json + the second session inside golden_attack_fixtures.json):
#
#   * 4-Aspect combo: each skill APPLIES its own hidden aspect marker (slot1 Warrior,
#     slot2 Mage, slot3 Healer, slot4 Rogue) and BRANCHES on whichever was active before:
#         active W -> Meteor +50% + burning ground field | Healing Oath +Holy Guard
#         active M -> Definitive Strike wide cleave +Armor Melted | Serpent's Kiss empowered
#         active H -> Meteor +Suppression | Serpent's Kiss +Concealed Blade
#         active R -> Definitive Strike +Prepared Strike | Healing Oath 2x-speed +Hallowed
#     (confirmed twice over: the effect auras per prior aspect AND the icon rebinds —
#     after an aspect lands, exactly its two branch skills swap to that aspect family's
#     icons, InfinityHero{A,B,C,D}{2,3}, with 15s shared IndexReset rings back to base.)
#   * Heroic resource 0-50: +1 per Aspect Effect applied (the captured Resource nodes
#     count 1,2,3... exactly on effect casts, never on autos or effect-less casts).
#     At 25 the marker aura "Heroic Empowerment" lands, slot 0 swaps to InfinityHeroUlt,
#     and the NEXT Heroic Strike is the sky-blade AoE: a 2.5s PlayerHitStream (W20 H10,
#     200ms ticks) that resets the pool to 0.
#   * The class was never itemized by AE (no catalog item carries MetaString 2022), so the
#     class armor is OURS: item 200022 in our homebrew band, wearing the "Hero of Infinity"
#     Kickstarter armor bundle (77678) with the classInfinityHero particle bundle (78541).
#
# Damage/heal MULTIPLIERS are ours (AE's server formula is invisible; ratios tuned to the
# captured bands: skills ~1.3-2x the auto, Meteor ~0.75x, the Mage-branch Serpent's Kiss
# ~2x its base). Everything else — node sequences, sounds, particles, hitboxes, icons,
# timings, aura names/flags — is capture-verbatim and replay-tested by test_infinity_hero.py.
INFINITY_HERO_CLASS_ID = 2022
INFINITY_HERO_ARMOR_ITEM = 200022
# v2/v3: attempts at the sky-blade visual that changed the particle node (Lifetime,
#        anchor, ordering). All reverted -- the decompiled client proves the node was
#        never at fault: NodeParticle ignores Lifetime entirely, and the cue only ever
#        spawns when its named animation actually plays.
# v4: the real fix -- the ult's PlayerAnimation Priority "Interrupt All" is the single
#     NodePlayerAnimation branch that enqueues instead of playing, so Attack1_Auto never
#     entered and the queued particle never drained. "Low" (what this class's own
#     working auto-attack uses) plays it. See the comment block in _IH_ULT.
INFINITY_HERO_GRAPH_VERSION = 4
CLASS_SHOP_ID = 2468                    # Gravelyn's Infinity — where classes are bought

_IH_RESOURCE = {"model": "heroic", "ResourceColor": 16773977, "MaxRP": 50,
                "Threshold": 25, "ThresholdColor": 16766720}
_IH_RIG = {
    "ID": INFINITY_HERO_ARMOR_ITEM,
    "Bundle": {"ID": 77678, "Name": "Hero of Infinity",
               "Filename": "armors/77678_AQ2DKSArmor.unity3d",
               "VersionStage": 2, "VersionLive": 2},
    "ClassParticleBundle": {"ID": 78541, "Name": "classInfinityHero_Default",
                            "Filename": "gameassets/classes/78541_classinfinityhero_default.unity3d",
                            "VersionStage": 1, "VersionLive": 1},
    "PrefabName": "ArmorSlots", "EquipSpot": 6, "ItemType": 21,
}
_IH_ITEM = {
    "ID": INFINITY_HERO_ARMOR_ITEM, "Name": "Infinity Hero",
    "Description": "The Hero of Infinity. Flow between the four Aspects — Warrior, Mage, "
                   "Healer, Rogue — chaining their arts into Aspect Effects; at 25 Heroic "
                   "the next Heroic Strike calls down the sky-blade.",
    "ItemType": 21, "EquipSpot": 6, "Linkage": "", "Icon": "iiclass", "Level": 1,
    "Quantity": 1, "StackSize": 1, "Element": 1, "Faction": 1, "MetaString": "2022",
    "DamageRange": 0.1, "Rarity": 50, "Filename": "armors/77678_AQ2DKSArmor.unity3d",
    "Coins": True, "PrefabName": "ArmorSlots", "MobileCompat": 1, "IsClass": True,
    "Bundle": _IH_RIG["Bundle"],
}

_IH_ASPECTS = ["Warrior Aspect", "Mage Aspect", "Healer Aspect", "Rogue Aspect"]
_ICO = "InfinityHero/InfinityHero"

# The combo rebind broadcast per newly-applied aspect (a StatusCode-3 Attack; capture-
# verbatim, including which two slots get branch icons + IndexReset revert rings and the
# Index quirks — A2/B2 carry Index 1, C2/D2 carry 0, exactly as AE sent them).
def _ih_ssi(slot, index, icon):
    return {"Name": "SetSkillIndex", "Slot": slot, "Index": index,
            "Icon": _ICO + icon, "hide": False}


def _ih_ir(slot, icon):
    return {"Name": "IndexReset", "Slot": slot, "Index": 0, "Time": 15000,
            "Icon": _ICO + icon, "CD": 0, "Shared": True, "Stay": False}


_IH_REBINDS = {
    "Warrior Aspect": [_ih_ssi(1, 0, "A1"), _ih_ssi(2, 1, "A2"), _ih_ir(2, "B1"),
                       _ih_ssi(3, 0, "A3"), _ih_ir(3, "C1"), _ih_ssi(4, 0, "D1")],
    "Mage Aspect":    [_ih_ssi(1, 1, "B2"), _ih_ir(1, "A1"), _ih_ssi(2, 0, "B1"),
                       _ih_ssi(3, 0, "C1"), _ih_ssi(4, 0, "B3"), _ih_ir(4, "D1")],
    "Healer Aspect":  [_ih_ssi(1, 0, "A1"), _ih_ssi(2, 0, "C2"), _ih_ir(2, "B1"),
                       _ih_ssi(3, 0, "C1"), _ih_ssi(4, 0, "C3"), _ih_ir(4, "D1")],
    "Rogue Aspect":   [_ih_ssi(1, 0, "D2"), _ih_ir(1, "A1"), _ih_ssi(2, 0, "B1"),
                       _ih_ssi(3, 0, "D3"), _ih_ir(3, "C1"), _ih_ssi(4, 0, "D1")],
}


# node builders (capture-verbatim prop shapes)
def _ih_restrict(anim, t, slots="2,3,4,5"):
    return [{"Name": "Restrict", "Direction": True, "Movement": True, "Skills": True,
             "Slot": slots, "Animation": anim, "ReleaseMode": "AtTime", "Time": t},
            {"Name": "Interruptable", "Animation": anim, "Time": t}]


def _ih_sfx(anim, sound, t=0.0):
    return {"Name": "SoundFX", "Animation": anim, "Sound": sound, "Time": t,
            "MinPitch": 0.0, "MaxPitch": 0.0}


def _ih_particle(anim, p, t, x, y, speed=None, follow="No Follow", life=None,
                 targets=None):
    node = {"Name": "Particle", "Follow": follow, "X": x, "Y": y, "Particle": p,
            "Time": t}
    if targets is not None:       # default (omitted) = on the caster
        node["Targets"] = targets
    if anim is not None:              # uncued spawns omit Animation entirely
        node["Animation"] = anim
    if speed is not None:
        node["AnimSpeed"] = speed
    if life is not None:
        node["Lifetime"] = life
    return node


# --- skill 169, Definitive Strike (slot 1, applies Warrior Aspect) --------------------------
def _ih_s1(sound, particle, hitbox, effect):
    """One Definitive Strike branch: lead-in + cleave + optional effect block."""
    seq = [{"Name": "Range", "HRange": 5.0, "VRange": 1.0, "Charge": True,
            "HoldAtRange": False},
           {"Name": "Cooldown", "Slot": 1, "CD": 3959, "Animation": ""}]
    seq += _ih_restrict("DS Skill1C", 0.45)
    seq += [sound, particle, hitbox,
            {"Name": "Damage", "DamageType": "Physical", "Multiplier": 1.3},
            {"Name": "DispenseDamage"}]
    seq += effect
    seq += [{"Do": "ApplyAura", "Aura": "Warrior Aspect"},
            {"Name": "UpdateAnimation", "Tag": "combatIdle", "Value": "2H_Fight"}]
    return seq


_IH_S1_HITBOX = {"Name": "AnimationHitbox", "X": 6.5, "Y": 0.0, "Width": 11, "Height": 2,
                 "Animation": "DS Skill1C", "Speed": 1.5, "Time": 0.35}
_IH_SKILL_169 = [
    {"Do": "Branch", "On": "aspect", "Cases": {
        # Mage active: fire-infused WIDE cleave, victims' armor melts
        "Mage Aspect": _ih_s1(
            _ih_sfx("DS Skill1C", "SFX_Impact_Fire_C", 0.3),
            _ih_particle("DS Skill1C", "classInfinityHero_S2_P4", "0.4", 0.0, 1.0),
            {"Name": "AnimationHitbox", "X": 0.0, "Y": 0.0, "Width": 19, "Height": 5,
             "Animation": "DS Skill1C", "Speed": 1.5, "Time": 0.35},
            [{"Do": "ResourceOp", "Op": "gain", "Amount": 1},
             {"Do": "ApplyAura", "Aura": "Armor Melted", "Targets": "@hits"},
             {"Name": "Resource"}]),
        # Rogue active: a poised strike, the next blow prepared
        "Rogue Aspect": _ih_s1(
            _ih_sfx("DS Skill1C", "sfx_rogue_viperskiss", 0.3),
            _ih_particle("DS Skill1C", "classInfinityHero_S2_P3", "0.35", 0.0, 3.0),
            _IH_S1_HITBOX,
            [{"Do": "ResourceOp", "Op": "gain", "Amount": 1},
             {"Do": "ApplyAura", "Aura": "Prepared Strike"},
             {"Name": "Resource"}]),
    },
     "Default": _ih_s1(
        _ih_sfx("DS Skill1C", "sfx_warrior_decisivestrike"),
        _ih_particle("DS Skill1C", "classInfinityHero_S2_P1", "0.4", 5.0, 3.0, speed=1.0),
        _IH_S1_HITBOX, [])},
]


# --- skill 170, Meteor (slot 2, applies Mage Aspect) ----------------------------------------
def _ih_s2(mid, tail):
    seq = [{"Name": "Range", "HRange": 31.0, "VRange": 31.0, "Charge": True,
            "HoldAtRange": False},
           {"Name": "Cooldown", "Slot": 2, "CD": 3959, "Animation": "Mage_CastOffensive3"}]
    seq += _ih_restrict("Mage_CastOffensive3", 0.5)
    seq += [{"Name": "Damage", "DamageType": "Magical", "Multiplier": 0.75}]
    seq += mid
    seq += tail
    seq += [{"Do": "ApplyAura", "Aura": "Mage Aspect"}]
    return seq


def _ih_meteor_spell(attach, graphic, impact, ease=True, impact_id=None):
    node = {"Name": "SpellAnimation", "FX": "Meteor", "Animation": "Mage_CastOffensive3",
            "SpellGraphic": graphic, "SpellImpact": impact, "AttachInit": "Origin",
            "Attach": attach, "AttachImpact": "Origin", "Follow": True,
            "X": 10.0, "Y": 15.0}
    if ease:
        node["Ease"] = "linear"
    if impact_id is not None:
        node["impactId"] = impact_id
    return node


_IH_SKILL_170 = [
    {"Do": "Branch", "On": "aspect", "Cases": {
        # Warrior active: +50%, and the impact leaves a burning field for 5s
        "Warrior Aspect": _ih_s2(
            [_ih_meteor_spell("Cast", "classInfinityHero_S3_P2",
                              "classInfinityHero_S3_P4", impact_id=1),
             {"Name": "ImpactSoundFX", "Animation": "classInfinityHero_S3_P4",
              "Sound": "SFX_Fire", "MinPitch": 0.0, "MaxPitch": 0.0},
             _ih_sfx("Mage_CastOffensive3", "sfx_mage_explosion")],
            [{"Do": "ResourceOp", "Op": "gain", "Amount": 1},
             {"Name": "Resource"},
             # the burning ground: an ASYNC packet that lands ~1s after the
             # cast, so its targets resolve against the world AT LANDING —
             # there is nothing left to set alight if the meteor's victim has
             # already died, and only the (empty) particle goes out, exactly
             # as the captured field packets show
             {"Do": "Packet", "Status": 4, "Delay": 1000, "Nodes": [
                 _ih_particle(None, "classInfinityHero_S3_P3", "0", 0.0, 0.0,
                              speed=1.0, life=7000.0, targets="@hits"),
                 {"Do": "Branch", "If": "hits > 0", "Then": [
                     {"Name": "PlayerHitStream", "X": 0.0, "Y": 0.0, "Width": 5.0,
                      "Height": 5.0, "Duration": 5000, "Interval": 500,
                      "Origin": "Target", "Slot": 2}]}]}]),
        # Healer active: the holy meteor, suppressing what it strikes
        "Healer Aspect": _ih_s2(
            [{"Name": "ImpactSoundFX", "Animation": "classInfinityHero_S3_P6",
              "Sound": "sfx_holy_meteor_impact", "MinPitch": 0.0, "MaxPitch": 0.0},
             _ih_sfx("Mage_CastOffensive3", "sfx_holy_meteor_cast"),
             _ih_particle("Mage_CastOffensive3", "classInfinityHero_S3_P5", "0.2",
                          0.0, 0.0, speed=1.0, targets="@target"),
             _ih_meteor_spell("Cast", "classInfinityHero_S3_P1",
                              "classInfinityHero_S3_P6", ease=False)],
            [{"Do": "ResourceOp", "Op": "gain", "Amount": 1},
             {"Do": "ApplyAura", "Aura": "Suppression", "Targets": "@hits"},
             {"Name": "Resource"}]),
    },
     "Default": _ih_s2(
        [{"Name": "ImpactSoundFX", "Animation": "classInfinityHero_S3_P4",
          "Sound": "SFX_Fire", "MinPitch": 0.0, "MaxPitch": 0.0},
         _ih_sfx("Mage_CastOffensive3", "sfx_mage_explosion"),
         _ih_meteor_spell("Origin", "classInfinityHero_S3_P1",
                          "classInfinityHero_S3_P4")], [])},
]


# --- skill 171, Healing Oath (slot 3, applies Healer Aspect) --------------------------------
def _ih_s3(mid):
    seq = [{"Name": "RangeMulti", "HRange": 31.0, "VRange": 31.0, "Target": "Self",
            "Targets": "@allies"},
           {"Name": "Cooldown", "Slot": 3, "CD": 7918, "Animation": ""}]
    seq += _ih_restrict("Healer_Cast2", 0.45)
    seq += [{"Name": "Damage", "Heal": True, "Multiplier": 10.0, "MaxTargets": 4,
             "Targets": "@allies"}]
    seq += mid
    seq += [{"Name": "PlayerAnimation", "Animation": "Healer_Cast2",
             "Priority": "Interrupt All", "Targets": "@allies"},
            {"Do": "ApplyAura", "Aura": "Healer Aspect"}]
    return seq


_IH_SKILL_171 = [
    {"Do": "Branch", "On": "aspect", "Cases": {
        # Warrior active: the oath becomes a guard
        "Warrior Aspect": _ih_s3(
            [_ih_particle("Healer_Cast2", "classInfinityHero_S4_P1", "0", 0.0, 8.0,
                          speed=1.0, follow="Follow", targets="@allies"),
             {"Do": "ResourceOp", "Op": "gain", "Amount": 1},
             {"Do": "ApplyAura", "Aura": "Holy Guard"},
             _ih_particle("Healer_Cast2", "classInfinityHero_S4_P2", "0", 0.0, 4.0,
                          speed=1.0, follow="Follow", life=5000.0),
             {"Name": "Resource"}]),
        # Rogue active: a swift oath (2x cast), footsteps hallowed behind you
        "Rogue Aspect": _ih_s3(
            [{"Do": "ResourceOp", "Op": "gain", "Amount": 1},
             {"Do": "ApplyAura", "Aura": "Hallowed Footsteps"},
             _ih_particle("Healer_Cast2", "classInfinityHero_S4_P3", "0", 0.0, 5.0,
                          speed=2.0, follow="Follow", targets="@allies"),
             {"Name": "Resource"}]),
    },
     "Default": _ih_s3(
        [_ih_particle("Healer_Cast2", "classInfinityHero_S4_P1", "0", 0.0, 8.0,
                      speed=1.0, follow="Follow", targets="@allies")])},
]


# --- skill 172, Serpent's Kiss (slot 4, applies Rogue Aspect) -------------------------------
def _ih_s4(sound, particle_args, effect, mult_scale=None):
    """One Serpent's Kiss branch. The mid-section swaps between the in-range
    strike and the gap-closing Dash lead-in (positional, per capture — the
    dash presses also skip Restrict AND the Heroic gain, an AE quirk we keep)."""
    p, px, py = particle_args
    dmg = {"Name": "Damage", "DamageType": "Physical", "Multiplier": 2.0}
    if mult_scale is not None:
        dmg["MultScale"] = mult_scale
    strike = (_ih_restrict("Rogue_Skill2", 0.4)
              + [_ih_sfx("Rogue_Skill2", sound),
                 _ih_particle("Rogue_Skill2", p, "0.25", px, py, speed=2.0)]
              + effect
              + [{"Name": "AnimationHitbox", "X": 5.7, "Y": 0.0, "Width": 10,
                  "Height": 2, "Animation": "Rogue_Skill2", "Speed": 2.0,
                  "Time": 0.25}])
    dash = [{"Name": "DashToTarget", "Face": True, "OffsetX": 1.5, "Duration": 300,
             "Async": True, "Animation": "Dash", "ForceMovement": False},
            _ih_sfx("Dash", sound),
            _ih_particle("Dash", p, "0", 0.0 if px == 5.0 else px, py,
                         follow="Follow Until Move"),
            {"Name": "AnimationHitbox", "X": 5.7, "Y": 0.0, "Width": 10, "Height": 2,
             "Animation": "Dash", "Speed": 1.0, "Time": 0.65}]
    aura = ([{"Do": "ApplyAura", "Aura": "Concealed Blade"}]
            if sound == "sfx_holy_thrust_cast" else [])
    return ([{"Name": "Range", "HRange": 31.0, "VRange": 31.0, "Charge": False,
              "HoldAtRange": False},
             {"Name": "Cooldown", "Slot": 4, "CD": 3959, "Animation": ""},
             {"Do": "Branch", "If": "dash", "Then": dash, "Else": strike},
             {"Name": "Damage2_MARKER"}]
            + [dmg, {"Name": "DispenseDamage"}]
            + aura
            + [{"Do": "ApplyAura", "Aura": "Rogue Aspect"},
               {"Name": "UpdateAnimation", "Tag": "combatIdle", "Value": "2H_Fight"}])


_IH_SKILL_172 = [
    {"Do": "Branch", "On": "aspect", "Cases": {
        # Healer active: the blessed thrust conceals a blade
        "Healer Aspect": _ih_s4("sfx_holy_thrust_cast",
                                ("classInfinityHero_S5_P3", 5.0, 1.0),
                                [{"Do": "ResourceOp", "Op": "gain", "Amount": 1},
                                 {"Name": "Resource"}]),
        # Mage active: spell-infused blades, roughly doubled
        "Mage Aspect": _ih_s4("SFX_Impact_Fire_C",
                              ("classInfinityHero_S5_P2", 5.0, 1.0),
                              [{"Do": "ResourceOp", "Op": "gain", "Amount": 1},
                               {"Name": "Resource"}],
                              mult_scale=2.0),
    },
     "Default": _ih_s4("sfx_rogue_viperskiss",
                       ("classInfinityHero_S5_P1", 8.0, 3.0), [])},
]

# drop the structural marker (the builder needs a stable split point between the
# positional Branch and the shared damage tail; the marker itself never ships)
def _ih_strip_markers(seq):
    out = []
    for e in seq:
        if isinstance(e, dict) and e.get("Name") == "Damage2_MARKER":
            continue
        if isinstance(e, dict):
            e = {k: (_ih_strip_markers(v) if isinstance(v, list) else v)
                 for k, v in e.items()}
        out.append(e)
    return out


_IH_SKILL_172 = _ih_strip_markers(_IH_SKILL_172)


# --- skill 168, Heroic Strike (slot 0 auto; at 25 Heroic -> the sky-blade) ------------------
_IH_AUTO_MELEE = [
    {"Name": "Range", "HRange": 31.0, "VRange": 31.0, "Charge": False,
     "HoldAtRange": False},
    {"Name": "Damage", "DamageType": "Physical", "Multiplier": 1.0},
    _ih_sfx("Attack1_Auto,Attack2,Attack3", "sfx_warrior_aa"),
    _ih_sfx("Attack1_Auto", "sfx_warrior_aa"),
    _ih_particle("Attack1_Auto", "classInfinityHero_S1_P1", "0", -2.0, 3.0),
    {"Name": "PlayerAnimation", "Animation": "Attack1_Auto", "Priority": "Low",
     "Speed": 1.0, "Targets": 1},
    {"Name": "UpdateAnimation", "Tag": "combatIdle", "Value": "2H_Fight"},
    {"Name": "Cooldown", "Slot": 0, "CD": 1979, "Animation": ""},
]
_IH_AUTO_RANGED = [
    {"Name": "Range", "HRange": 31.0, "VRange": 31.0, "Charge": False,
     "HoldAtRange": False},
    {"Name": "Damage", "DamageType": "Physical", "Multiplier": 1.0},
    _ih_sfx("Attack1_Auto", "sfx_ranged_swing_cast"),
    {"Name": "ImpactSoundFX", "Animation": "classInfinityHero_S1_P6",
     "Sound": "sfx_ranged_swing_impact", "MinPitch": 0.0, "MaxPitch": 0.0},
    {"Name": "SpellAnimation", "FX": "Projectile", "Animation": "Attack1_Auto",
     "SpellGraphic": "classInfinityHero_S1_P5", "SpellImpact": "classInfinityHero_S1_P6",
     "AttachInit": "Origin", "Attach": "Cast", "AttachImpact": "Cast",
     "Ease": "linear", "ProjSpeed": 60.0, "Follow": True},
    {"Name": "UpdateAnimation", "Tag": "combatIdle", "Value": "2H_Fight"},
    {"Name": "Cooldown", "Slot": 0, "CD": 1979, "Animation": ""},
]
_IH_ULT = [
    {"Name": "Range", "HRange": 31.0, "VRange": 31.0, "Charge": True,
     "HoldAtRange": False},
    {"Do": "SetVar", "Var": "armed", "Expr": "0"},
    {"Do": "ResourceOp", "Op": "set", "Amount": 0},
    {"Name": "Resource"},
    {"Name": "Cooldown", "Slot": 0, "CD": 1979, "Animation": ""},
] + _ih_restrict("Attack1_Auto", 0.4, slots="1,2,3,4,5") + [
    _ih_sfx("Attack1_Auto", "sfx_hero_ultimate_cast"),
    # --- the sky-blade visual ---------------------------------------------------------
    # The particle node itself is AE's verbatim (it was never the problem). Read the
    # decompiled client to settle this instead of guessing:
    #
    #   NodeParticle.Execute -- a node carrying Animation+Time NEVER spawns immediately.
    #   Unless the name is the caster's idle/combatIdle state it lands in
    #   animation.queuedParticles[<animation>], and OnAnimationExit drains that queue
    #   only when the animator actually ENTERS that state. So the particle appears if
    #   and only if the named animation really plays.
    #
    #   NodePlayerAnimation.Execute -- for the main player with no damage queue (this
    #   cast has no Damage node; its damage is the PlayerHitStream), Priority
    #   "Interrupt All" is the ONE branch that does `queuedAnimations.Enqueue(...);
    #   return;` instead of `animation.Play(...)`. Every other priority plays the state
    #   directly. Enqueued behind an empty damage queue, Attack1_Auto was never entered,
    #   so the queued S1_P4 cue was never drained -- stacks spent, no sword.
    #
    # "Low" is what this class's own working auto-attack uses with the same animation
    # and the same queue key (the S1_P1 auto particle renders fine), so it is the
    # proven-good value here rather than another guess.
    #
    # NOTE: "Lifetime" is dead weight -- NodeParticle never reads it; SpawnParticle
    # hardcodes TimedKill = 3f. Kept only to stay byte-identical to AE's node.
    _ih_particle("Attack1_Auto", "classInfinityHero_S1_P4", "0", -0.5, 10.0,
                 speed=1.0, life=4000.0),
    {"Name": "PlayerHitStream", "X": 0.0, "Y": 0.0, "Width": 20.0, "Height": 10.0,
     "Duration": 2500, "Interval": 200, "Origin": "Self", "Slot": 0},
    {"Name": "PlayerAnimation", "Animation": "Attack1_Auto",
     "Priority": "Low", "Speed": 1.0, "Targets": 1},
    {"Name": "SetSkillIndex", "Slot": 0, "Index": 0, "Icon": _ICO + "AA1"},
    {"Name": "UpdateAnimation", "Tag": "combatIdle", "Value": "2H_Fight"},
]
_IH_SKILL_168 = [
    {"Do": "Branch", "If": "armed", "Then": _IH_ULT,
     "Else": [{"Do": "Branch", "If": "ranged",
               "Then": _IH_AUTO_RANGED, "Else": _IH_AUTO_MELEE}]},
]

# runs after every skill press (slots 1-4): arm the ultimate on crossing 25
# Heroic, refresh the armed marker, broadcast the combo rebind for the new aspect
_IH_POST = [
    {"Do": "Formula", "Var": "was_armed", "Expr": "armed"},
    {"Do": "Branch", "If": "rp >= 25 and not was_armed", "Then": [
        {"Do": "SetVar", "Var": "armed", "Expr": "1"},
        {"Do": "ApplyAura", "Aura": "Heroic Empowerment"},
        {"Do": "Emit", "Node": {"Name": "UpdateAnimation", "Tag": "combatIdle",
                                "Value": "2H_Fight"}},
    ]},
    {"Do": "Branch", "If": "was_armed and armed", "Then": [
        {"Do": "Emit", "Node": {"Name": "Aura", "AuraName": "Heroic Empowerment",
                                "Hide": True, "Targets": []}},
        {"Do": "Emit", "Node": {"Name": "UpdateAnimation", "Tag": "combatIdle",
                                "Value": "2H_Fight"}},
    ]},
    {"Do": "Branch", "On": "aspect",
     "Cases": {a: [{"Do": "Packet", "Status": 3, "Nodes": _IH_REBINDS[a]}]
               for a in _IH_ASPECTS}},
    {"Do": "Branch", "If": "rp >= 25 and not was_armed and armed", "Then": [
        {"Do": "Packet", "Status": 3, "Nodes": [
            {"Name": "SetSkillIndex", "Slot": 0, "Index": 1, "Icon": _ICO + "Ult"},
            _ih_ir(0, "AA1")]},
    ]},
]

INFINITY_HERO_RULES = {
    "engine": 1,
    "resource": {"model": "heroic", "max": 50, "arm_at": 25},
    "vars": {"armed": 0, "ranged": 0, "dash": 0},
    "post_slots": [1, 2, 3, 4],
    "post": _IH_POST,
    "skills": {"168": _IH_SKILL_168, "169": _IH_SKILL_169, "170": _IH_SKILL_170,
               "171": _IH_SKILL_171, "172": _IH_SKILL_172},
}

# slot -> (skill_id, name, icon, description, SkillForge display graph). The RULE CONFIG
# above is what the engine executes (the branches can't be drawn as a linear chain); these
# graphs are the base branch, so the Forge shows each skill's real node anatomy.
_IH_SKILLS = [
    (0, 168, "Heroic Strike", _ICO + "AA1",
     "Strike with the might of the Infinity Hero. At 25 Heroic, your next Heroic Strike "
     "becomes Heroic Empowerment: a rain of sky-blades over 2.5 seconds.",
     [(str(i), n) for i, n in enumerate([{"Name": "OnRequest"}] + [
         n for n in _IH_AUTO_MELEE if "Name" in n])]),
    (1, 169, "Definitive Strike", _ICO + "A1",
     "A decisive cleave that assumes the Warrior Aspect. Mage Aspect: a wide flame arc "
     "that melts armor (+1 Heroic). Rogue Aspect: prepares your next strike (+1 Heroic).",
     [(str(i), n) for i, n in enumerate([{"Name": "OnRequest"}] + [
         n for n in _ih_s1(_ih_sfx("DS Skill1C", "sfx_warrior_decisivestrike"),
                           _ih_particle("DS Skill1C", "classInfinityHero_S2_P1", "0.4",
                                        5.0, 3.0, speed=1.0),
                           _IH_S1_HITBOX, []) if "Name" in n])]),
    (2, 170, "Meteor", _ICO + "B1",
     "Call down a meteor and assume the Mage Aspect. Warrior Aspect: +50% damage and a "
     "burning field for 5 seconds (+1 Heroic). Healer Aspect: applies Suppression "
     "(+1 Heroic).",
     [(str(i), n) for i, n in enumerate([{"Name": "OnRequest"}] + [
         n for n in _ih_s2([{"Name": "ImpactSoundFX",
                             "Animation": "classInfinityHero_S3_P4", "Sound": "SFX_Fire",
                             "MinPitch": 0.0, "MaxPitch": 0.0},
                            _ih_sfx("Mage_CastOffensive3", "sfx_mage_explosion"),
                            _ih_meteor_spell("Origin", "classInfinityHero_S3_P1",
                                             "classInfinityHero_S3_P4")], [])
         if "Name" in n])]),
    (3, 171, "Healing Oath", _ICO + "C1",
     "Heal yourself and nearby allies, assuming the Healer Aspect. Warrior Aspect: also "
     "grants Holy Guard (+1 Heroic). Rogue Aspect: cast at double speed, leaving Hallowed "
     "Footsteps (+1 Heroic).",
     [(str(i), n) for i, n in enumerate([{"Name": "OnRequest"}] + [
         n for n in _ih_s3([_ih_particle("Healer_Cast2", "classInfinityHero_S4_P1",
                                         "0", 0.0, 8.0, speed=1.0, follow="Follow")])
         if "Name" in n])]),
    (4, 172, "Serpent's Kiss", _ICO + "D1",
     "A lightning-fast twin strike that assumes the Rogue Aspect. Healer Aspect: "
     "conceals a blade for your next strike (+1 Heroic). Mage Aspect: spell-infused "
     "blades deal doubled damage (+1 Heroic).",
     [(str(i), n) for i, n in enumerate([{"Name": "OnRequest"}] + [
         n for n in _ih_strip_markers(
             _ih_s4("sfx_rogue_viperskiss", ("classInfinityHero_S5_P1", 8.0, 3.0), []))
         if isinstance(n, dict) and "Name" in n])]),
]


def seed_infinity_hero(conn):
    """Seed the Infinity Hero class 2022: class row (rig + heroic resource), the rule
    config into classes.raw, the five skill rows + slot links, and the class-granting
    armor item 200022. Same non-clobbering rules as seed_paladin. Returns # skills."""
    import forge
    import db as _db
    row = conn.execute("SELECT v FROM kv WHERE k='infinity_hero_graph_version'").fetchone()
    stored = int(row["v"]) if row and str(row["v"]).isdigit() else 0
    refresh = stored < INFINITY_HERO_GRAPH_VERSION

    rig_json = json.dumps(_IH_RIG, separators=(",", ":"))
    conn.execute(
        "INSERT INTO classes(class_id, name, bundle, rig, resource) VALUES(?,?,?,?,?) "
        "ON CONFLICT(class_id) DO NOTHING",
        (INFINITY_HERO_CLASS_ID, "Infinity Hero", "", rig_json,
         json.dumps(_IH_RESOURCE, separators=(",", ":"))))
    conn.execute("UPDATE classes SET rig=? WHERE class_id=? AND (rig IS NULL OR rig='')",
                 (rig_json, INFINITY_HERO_CLASS_ID))
    if refresh:
        _seed_class_rules(conn, INFINITY_HERO_CLASS_ID, INFINITY_HERO_RULES)
    _db.store_item(conn, _IH_ITEM)                     # the class armor (insert-if-absent)
    # Sell it where every other class is sold (Gravelyn's Infinity, the class shop) on the
    # same free/coin-flagged terms as the base classes — otherwise the class exists but no
    # player can reach it. Insert-if-absent, so a later price edit sticks.
    conn.execute(
        "INSERT INTO shop_items(shop_id, shop_item_id, item_id, cost, coins, quantity_remain) "
        "VALUES(?,?,?,?,?,?) ON CONFLICT(shop_id, shop_item_id) DO NOTHING",
        (CLASS_SHOP_ID, INFINITY_HERO_ARMOR_ITEM, INFINITY_HERO_ARMOR_ITEM, 0, 1, -1))

    n = 0
    for slot, skill_id, name, icon, desc, node_list in _IH_SKILLS:
        data, forge_data = forge.linear_graph(node_list)
        srow = conn.execute("SELECT data FROM skills WHERE skill_id=?", (skill_id,)).fetchone()
        cur = (srow["data"] or "").replace(" ", "") if srow else ""
        empty = (srow is None) or (not cur) or cur in ("[{},{}]", "[]", "null")
        if empty or refresh:
            conn.execute(
                "INSERT INTO skills(skill_id, action, name, description, icon, slot, data, forge_data) "
                "VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(skill_id) DO UPDATE SET "
                "name=excluded.name, description=excluded.description, icon=excluded.icon, "
                "data=excluded.data, forge_data=excluded.forge_data",
                (skill_id, 1 if slot == 0 else 0, name, desc, icon, slot,
                 json.dumps(data, separators=(",", ":")),
                 json.dumps(forge_data, separators=(",", ":"))))
            n += 1
        conn.execute(
            "INSERT INTO class_skills(class_id, slot, skill_id) VALUES(?,?,?) "
            "ON CONFLICT(class_id, slot) DO NOTHING",
            (INFINITY_HERO_CLASS_ID, slot, skill_id))
    conn.execute("INSERT INTO kv(k,v) VALUES('infinity_hero_graph_version',?) "
                 "ON CONFLICT(k) DO UPDATE SET v=excluded.v",
                 (str(INFINITY_HERO_GRAPH_VERSION),))
    return n


# --- Chronomancer (class 2099) ---------------------------------------------------------
# OUR five-button class (auto included), deliberately authored with the existing Skill Forge
# node vocabulary. Chrono Charges are a 0..12 stacking pool. Time Dilation and Temporal Stasis
# are normal Aura nodes whose mechanics are server-owned in combat.py; InfinityLoader mirrors
# their AnimationSpeed metadata on only the affected monster Animators.
CHRONOMANCER_CLASS_ID = 2099
CHRONOMANCER_ARMOR_ITEM = 200024
CHRONOMANCER_GRAPH_VERSION = 3
_CHRONO_SKILL_IDS = [90410, 90411, 90412, 90413, 90414]
_CHRONO_ICON = "InfinityHero/InfinityHero"
_CHRONO_RESOURCE = {"model": "stacking", "ResourceColor": 6737151, "MaxRP": 12,
                    "Threshold": 12, "ThresholdColor": 16766720}
_CHRONO_RIG = {
    "ID": CHRONOMANCER_ARMOR_ITEM,
    "Bundle": {"ID": 8814, "Name": "Doctor When",
               "Filename": "armors/8814_DoctorWho.unity3d",
               "VersionStage": 4, "VersionLive": 4},
    "PrefabName": "ArmorSlots", "EquipSpot": 6, "ItemType": 21,
}
_CHRONO_ITEM = {
    "ID": CHRONOMANCER_ARMOR_ITEM, "Name": "TimeLord",
    "Description": "A wandering master of time and relative dimensions. Build up to 12 "
                   "Chrono Charges, steal an enemy's seconds, then bring about the End of Time.",
    "ItemType": 21, "EquipSpot": 6, "Linkage": "", "Icon": "iiclass", "Level": 1,
    "Quantity": 1, "StackSize": 1, "Element": 1, "Faction": 1,
    "MetaString": str(CHRONOMANCER_CLASS_ID), "DamageRange": 0.1, "Rarity": 50,
    "Filename": "armors/8814_DoctorWho.unity3d", "Coins": True,
    "PrefabName": "ArmorSlots", "MobileCompat": 1, "IsClass": True,
    "Bundle": _CHRONO_RIG["Bundle"],
}


def _chrono_graph(cd, multiplier=0, magical=True, targets="@target", max_targets=1,
                  animation="Mage_CastOffensive3", hits=1):
    nodes = [("0", {"Name": "OnRequest"}),
             ("1", {"Name": "Range", "HRange": 22, "VRange": 8}),
             ("2", {"Name": "Cooldown", "CD": cd}),
             ("3", {"Name": "PlayerAnimation", "Animation": animation})]
    for i in range(hits):
        nodes.append((str(4 + i), {"Name": "Damage",
                     "DamageType": "Magical" if magical else "Physical",
                     "Multiplier": multiplier, "Targets": targets,
                     "MaxTargets": max_targets}))
    return nodes


_CHRONO_SKILLS = [
    (0, 90410, "Temporal Strike", _CHRONO_ICON + "AA1",
     "A measured strike that generates 1 Chrono Charge.",
     _chrono_graph(1800, 1.0, False, animation="Attack1_Auto,Attack2,Attack3")),
    (1, 90411, "Echoed Blow", _CHRONO_ICON + "A1",
     "Fold one attack across two moments, striking twice. Generates 2 Chrono Charges.",
     _chrono_graph(4500, 0.75, True, hits=2)),
    (2, 90412, "Rewind", _CHRONO_ICON + "C1",
     "Rewind your wounds, restoring health from spell power. Generates 2 Chrono Charges.",
     [("0", {"Name": "OnRequest"}), ("1", {"Name": "Cooldown", "CD": 9000}),
      ("2", {"Name": "PlayerAnimation", "Animation": "Healer_Cast2"}),
      ("3", {"Name": "Damage", "Heal": True, "DamageType": "Magical",
             "Multiplier": 1.2, "Targets": "@self", "MaxTargets": 1})]),
    (3, 90413, "Stolen Seconds", _CHRONO_ICON + "B1",
     "Damage up to four enemies and slow their attacks, casts, and animations by 65% for 6 seconds. "
     "Generates 3 Chrono Charges.",
     _chrono_graph(10000, 1.1, True, "@enemies", 4) +
     [("4a", {"Name": "Aura", "AuraName": "Time Dilation", "Targets": "@hits"})]),
    (4, 90414, "End of Time", _CHRONO_ICON + "D1",
     "Consume every Chrono Charge for an expanding time rupture, then freeze surviving enemies "
     "for 2.5 seconds. Damage increases by 8% per charge consumed.",
     _chrono_graph(22000, 1.4, True, "@enemies", 6) +
     [("4a", {"Name": "Aura", "AuraName": "Temporal Stasis", "Targets": "@hits"})]),
]

CHRONOMANCER_RULES = {
    "engine": 1, "resource": {"model": "stacking", "max": 12},
    "skills": {
        "90410": [{"Do": "ResourceOp", "Op": "gain", "Amount": 1}, {"Do": "Graph"}],
        "90411": [{"Do": "ResourceOp", "Op": "gain", "Amount": 2}, {"Do": "Graph"}],
        "90412": [{"Do": "ResourceOp", "Op": "gain", "Amount": 2}, {"Do": "Graph"}],
        "90413": [{"Do": "ResourceOp", "Op": "gain", "Amount": 3}, {"Do": "Graph"}],
        "90414": [{"Do": "ResourceOp", "Op": "spend_all"},
                  {"Do": "Graph", "Overlay": {"Damage": {
                      "MultScale": {"$": "1 + 0.08*spent"}}}}],
    },
}


def seed_chronomancer(conn):
    """Seed the five-skill Chronomancer, class armor, rules, and class-shop link."""
    import forge
    import db as _db
    row = conn.execute("SELECT v FROM kv WHERE k='chronomancer_graph_version'").fetchone()
    stored = int(row["v"]) if row and str(row["v"]).isdigit() else 0
    refresh = stored < CHRONOMANCER_GRAPH_VERSION
    rig_json = json.dumps(_CHRONO_RIG, separators=(",", ":"))
    conn.execute(
        "INSERT INTO classes(class_id,name,bundle,rig,resource) VALUES(?,?,?,?,?) "
        "ON CONFLICT(class_id) DO NOTHING",
        (CHRONOMANCER_CLASS_ID, "TimeLord", "", rig_json,
         json.dumps(_CHRONO_RESOURCE, separators=(",", ":"))))
    conn.execute("UPDATE classes SET name=?,rig=?,resource=? WHERE class_id=?",
                 ("TimeLord", rig_json, json.dumps(_CHRONO_RESOURCE, separators=(",", ":")),
                  CHRONOMANCER_CLASS_ID))
    if refresh:
        _seed_class_rules(conn, CHRONOMANCER_CLASS_ID, CHRONOMANCER_RULES)
    _db.store_item(conn, _CHRONO_ITEM, replace=True)
    conn.execute(
        "INSERT INTO shop_items(shop_id,shop_item_id,item_id,cost,coins,quantity_remain) "
        "VALUES(?,?,?,?,?,?) ON CONFLICT(shop_id,shop_item_id) DO NOTHING",
        (CLASS_SHOP_ID, CHRONOMANCER_ARMOR_ITEM, CHRONOMANCER_ARMOR_ITEM, 0, 1, -1))
    n = 0
    for slot, skill_id, name, icon, desc, node_list in _CHRONO_SKILLS:
        data, forge_data = forge.linear_graph(node_list)
        srow = conn.execute("SELECT data FROM skills WHERE skill_id=?", (skill_id,)).fetchone()
        cur = (srow["data"] or "").replace(" ", "") if srow else ""
        if srow is None or not cur or cur in ("[{},{}]", "[]", "null") or refresh:
            conn.execute(
                "INSERT INTO skills(skill_id,action,name,description,icon,slot,data,forge_data) "
                "VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(skill_id) DO UPDATE SET "
                "name=excluded.name,description=excluded.description,icon=excluded.icon,"
                "slot=excluded.slot,data=excluded.data,forge_data=excluded.forge_data",
                (skill_id, 1 if slot == 0 else 0, name, desc, icon, slot,
                 json.dumps(data, separators=(",", ":")),
                 json.dumps(forge_data, separators=(",", ":"))))
            n += 1
        conn.execute("INSERT INTO class_skills(class_id,slot,skill_id) VALUES(?,?,?) "
                     "ON CONFLICT(class_id,slot) DO UPDATE SET skill_id=excluded.skill_id",
                     (CHRONOMANCER_CLASS_ID, slot, skill_id))
    conn.execute("INSERT INTO kv(k,v) VALUES('chronomancer_graph_version',?) "
                 "ON CONFLICT(k) DO UPDATE SET v=excluded.v",
                 (str(CHRONOMANCER_GRAPH_VERSION),))
    return n


def seed_skill_graphs(conn):
    """Give the Dragonslayer kit its node-graphs (reconstructed from the capture). Seeds
    empty skills always; force-refreshes our seeded graphs once per SKILL_GRAPH_VERSION
    bump so fixes land, but never clobbers a user-authored graph on an up-to-date DB."""
    import forge
    row = conn.execute("SELECT v FROM kv WHERE k='skill_graph_version'").fetchone()
    stored = int(row["v"]) if row and str(row["v"]).isdigit() else 0
    refresh = stored < SKILL_GRAPH_VERSION          # a new canonical version -> re-apply once
    n = 0
    for skill_id, node_list in [(165, _DS_AUTO_NODES), (167, _DS_SCORCHED),
                                (103, _DS_IMPALE), (104, _DS_INCAP), (105, _DS_BANE)]:
        row = conn.execute("SELECT data FROM skills WHERE skill_id=?", (skill_id,)).fetchone()
        if row is None:
            continue
        cur = (row["data"] or "").replace(" ", "")
        empty = (not cur) or cur in ("[{},{}]", "[]", "null")
        if not empty and not refresh:
            continue                          # up-to-date DB -> leave authored graphs alone
        data, forge_data = forge.linear_graph(node_list)
        conn.execute("UPDATE skills SET data=?, forge_data=? WHERE skill_id=?",
                     (json.dumps(data, separators=(",", ":")),
                      json.dumps(forge_data, separators=(",", ":")), skill_id))
        n += 1

    # the other classes' skills (Warrior/Healer/Mage/Rogue) — mined from the capture into
    # data/skill_graphs.json. Same empty/refresh rule; the hand-tuned DS five above win.
    ds_ids = {165, 167, 103, 104, 105}
    if SKILL_GRAPHS_FILE.exists():
        try:
            mined = json.loads(SKILL_GRAPHS_FILE.read_text(encoding="utf-8"))
        except Exception:
            mined = {}
        for sid_s, g in mined.items():
            sid = int(sid_s)
            if sid in ds_ids:
                continue
            row = conn.execute("SELECT data FROM skills WHERE skill_id=?", (sid,)).fetchone()
            if row is None:
                continue                       # not a skill we serve
            cur = (row["data"] or "").replace(" ", "")
            empty = (not cur) or cur in ("[{},{}]", "[]", "null")
            if not empty and not refresh:
                continue
            g_data = g["data"]
            dmg = SKILL_DAMAGE.get(sid)
            if dmg:                                # author element + multiplier (P1-4)
                g_data = _author_damage(g_data, *dmg)
            g_data = _author_mage_fx(g_data, sid)
            conn.execute("UPDATE skills SET data=?, forge_data=? WHERE skill_id=?",
                         (json.dumps(g_data, separators=(",", ":")),
                          json.dumps(g["forge"], separators=(",", ":")), sid))
            n += 1

    n += _patch_mage_spell_fx(conn)
    conn.execute("INSERT INTO kv(k,v) VALUES('skill_graph_version',?) "
                 "ON CONFLICT(k) DO UPDATE SET v=excluded.v", (str(SKILL_GRAPH_VERSION),))
    return n


def grant_class_items(conn):
    """Maintain class-item ownership. Base classes are NOT auto-granted — players BUY them at the
    class shop (Gravelyn's Infinity); existing owners keep what they already have. This reconciles
    class POINTS to a consistent maxed CP (P2-1: the live DB had a 1/302499/302500 split), dedupes
    class-item rows, and enforces the STAFF-ONLY class gate (Big Jake). Returns # of changes."""
    item_ids = class_item_ids(conn)
    # reconcile: every owned class item -> the consistent maxed CP (heals the 1/302499 rows)
    for iid in item_ids:
        conn.execute("UPDATE char_items SET quantity=? WHERE item_id=? AND quantity<>?",
                     (CLASS_CP_MAX, iid, CLASS_CP_MAX))
    # heal the char_item_id counter if it ever fell behind the real max (defensive)
    mx = conn.execute("SELECT MAX(char_item_id) AS m FROM char_items").fetchone()["m"] or 0
    if int(db.kv_get(conn, "next_char_item_id", "1")) <= mx:
        db.kv_set(conn, "next_char_item_id", mx + 1)
    # dedupe class-item rows (you can't own two of the same class) — keep the richest
    # (highest quantity = real class points / equipped), drop the rest. Fixes buy+grant dups.
    for iid in item_ids:
        for ch in conn.execute("SELECT char_id FROM char_items WHERE item_id=? "
                               "GROUP BY char_id HAVING COUNT(*)>1", (iid,)).fetchall():
            keep = conn.execute(
                "SELECT char_item_id FROM char_items WHERE char_id=? AND item_id=? "
                "ORDER BY equipped DESC, quantity DESC, char_item_id LIMIT 1",
                (ch["char_id"], iid)).fetchone()["char_item_id"]
            conn.execute("DELETE FROM char_items WHERE char_id=? AND item_id=? AND char_item_id<>?",
                         (ch["char_id"], iid, keep))
    # Enforce the STAFF-ONLY class gate: strip those armors from non-staff, grant to staff.
    # (Base classes are intentionally NOT auto-granted anymore — bought at the class shop.)
    changed = 0
    staff_ids = [r["id"] for r in conn.execute(
        "SELECT id FROM characters WHERE COALESCE(access_level, 0) >= ?", (STAFF_CLASS_ACCESS,))]
    for iid in STAFF_ONLY_CLASS_ITEMS:
        if not conn.execute("SELECT 1 FROM items WHERE item_id=?", (iid,)).fetchone():
            continue                                       # that staff class isn't in this DB
        for ch in conn.execute(
                "SELECT id, class_id FROM characters WHERE COALESCE(access_level, 0) < ?",
                (STAFF_CLASS_ACCESS,)).fetchall():
            if conn.execute("SELECT 1 FROM char_items WHERE char_id=? AND item_id=?",
                            (ch["id"], iid)).fetchone():
                conn.execute("DELETE FROM char_items WHERE char_id=? AND item_id=?", (ch["id"], iid))
                if int(ch["class_id"] or 0) == BIG_JAKE_CLASS_ID:   # had it equipped -> fall back
                    conn.execute("UPDATE characters SET class_id=? WHERE id=?",
                                 (STARTER_CLASS_ID, ch["id"]))
                changed += 1
        for sid in staff_ids:
            if not conn.execute("SELECT 1 FROM char_items WHERE char_id=? AND item_id=?",
                                (sid, iid)).fetchone():
                cid = int(db.kv_get(conn, "next_char_item_id", "1"))
                db.kv_set(conn, "next_char_item_id", cid + 1)
                conn.execute("INSERT INTO char_items(char_item_id, char_id, item_id, quantity, "
                             "equipped, banked, loot_id) VALUES(?,?,?,?,0,0,-1)",
                             (cid, sid, iid, CLASS_CP_MAX))
                changed += 1
    return changed


def seed_items(conn):
    """Shared item catalog from data/items.json (item_id -> item def). The authoritative
    source after export_catalog.py; was previously only in the live DB."""
    if not ITEMS_FILE.exists():
        return 0
    try:
        items = json.loads(ITEMS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return 0
    for iid, it in items.items():
        i2 = dict(it)
        i2.setdefault("ID", int(iid))
        db.store_item(conn, i2)
    return len(items)


# --- Practice Spellstone ---------------------------------------------------------------
# A minimal real ItemType 44 implementation for the client RequestUseSpellstone path. The
# item links to its standalone Skill Forge graph through MetaString, just like a class armor
# links to a class through MetaString without embedding behavior in the item itself.
PRACTICE_SPELLSTONE_ITEM_ID = 200023
PRACTICE_SPELLSTONE_SKILL_ID = 90390
PRACTICE_SPELLSTONE_ITEM = {
    "ID": PRACTICE_SPELLSTONE_ITEM_ID,
    "Name": "Practice Frogzard Spellstone",
    "Description": "A beginner Spellstone forged to transform its user into a Frogzard for "
                   "30 seconds. Equip it to spell slot 6, then press 6 to cast. Reusable.",
    "ItemType": 44, "EquipSpot": 1, "Linkage": "", "Icon": "iibag", "Level": 1,
    "Quantity": 1, "StackSize": 1, "Element": 1, "Faction": 1, "strReqQuests": "",
    "MetaString": str(PRACTICE_SPELLSTONE_SKILL_ID), "DamageRange": 0.1, "Rarity": 1,
    "MobileCompatibility": 1, "Cost": 0, "Coins": True, "ReqQuests": [], "boostValues": {},
}
PRACTICE_SPELLSTONE_NODES = [
    ("0", {"Name": "OnRequest"}),
    ("1", {"Name": "Cooldown", "CD": 30000}),
    ("2", {"Name": "MonTransform", "Linkage": "Frogzard", "Scale": 1.0,
           "Bundle": {"ID": 46555, "Name": "frogzard",
                      "Filename": "npcs/46555_frogzard.unity3d",
                      "VersionStage": 8, "VersionLive": 8}}),
    ("3", {"Name": "Aura", "AuraName": "Practice Frogzard Form",
           "Duration": 30, "MaxTargets": 1}),
]


def seed_practice_spellstone(conn):
    """Seed the reusable stone plus its standalone Skill Forge graph. Refresh our reserved
    IDs on every seed so code, item metadata, and graph cannot drift apart."""
    import forge
    db.store_item(conn, PRACTICE_SPELLSTONE_ITEM, replace=True)
    data, forge_data = forge.linear_graph(PRACTICE_SPELLSTONE_NODES)
    conn.execute(
        "INSERT INTO skills(skill_id, action, name, description, icon, slot, data, forge_data) "
        "VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(skill_id) DO UPDATE SET "
        "name=excluded.name, description=excluded.description, icon=excluded.icon, "
        "slot=excluded.slot, data=excluded.data, forge_data=excluded.forge_data",
        (PRACTICE_SPELLSTONE_SKILL_ID, 0, "Practice Frogzard Form",
         "Transform into a Frogzard for 30 seconds.", "iibag", -1,
         json.dumps(data, separators=(",", ":")),
         json.dumps(forge_data, separators=(",", ":"))))
    return 1


def seed_shops(conn):
    """Shops from data/shops.json: shop meta + shop_item links (items come from
    seed_items). Reproduces every captured/imported shop, not just the one sample."""
    if not SHOPS_FILE.exists():
        return 0, 0
    try:
        shops = json.loads(SHOPS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return 0, 0
    links = 0
    for sid, s in shops.items():
        db.store_shop(conn, s.get("meta", {}), shop_id=int(sid))
        for li in s.get("items") or []:
            conn.execute(
                "INSERT INTO shop_items(shop_id, shop_item_id, item_id, cost, coins, "
                "quantity_remain) VALUES(?,?,?,?,?,?) ON CONFLICT(shop_id, shop_item_id) DO NOTHING",
                (int(sid), li["shop_item_id"], li["item_id"], li["cost"],
                 li["coins"], li["quantity_remain"]))
            links += 1
    return len(shops), links


def seed_defaultclasses(conn):
    """The char-creation base classes (name -> armor bundle) into kv 'defaultclasses',
    so the login handler serves them from the DB instead of a capture sample."""
    if not DEFAULTCLASSES_FILE.exists():
        return 0
    try:
        classes = json.loads(DEFAULTCLASSES_FILE.read_text(encoding="utf-8"))
    except Exception:
        return 0
    db.kv_set(conn, "defaultclasses", json.dumps(classes, separators=(",", ":")))
    return len(classes)


def seed_monster_drops(conn):
    """Per-monster drop tables from data/monster_drops.json — {"<MonID>": [{item_id, rate, quantity}]}.
    `rate` is the item's INDEPENDENT 0..1 chance to drop on a kill. Idempotent (insert-if-absent);
    non-numeric keys (e.g. a "_comment") are skipped. Empty/absent file = no per-monster drops
    (a monster then drops only the global_drops table, if any). Returns the number of rows seeded."""
    try:
        data = json.loads(MONSTER_DROPS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return 0
    n = 0
    for mid, rows in (data or {}).items():
        if not str(mid).lstrip("-").isdigit():
            continue                                   # skip "_comment"/doc keys
        for r in rows or []:
            try:
                item_id = int(r["item_id"])
            except (KeyError, TypeError, ValueError):
                continue
            conn.execute(
                "INSERT INTO monster_drops(mon_id, item_id, rate, quantity) VALUES(?,?,?,?) "
                "ON CONFLICT(mon_id, item_id) DO NOTHING",
                (int(mid), item_id, float(r.get("rate", 0.1) or 0.1), int(r.get("quantity", 1) or 1)))
            n += 1
    return n


def seed_global_drops(conn):
    """Global drop table from data/global_drops.json — {"<ItemID>": {rate, quantity}} that EVERY
    monster rolls on top of its own drops (e.g. gems). Idempotent (insert-if-absent); rows whose
    item isn't in the catalog are skipped (FK). Returns the number of rows seeded."""
    f = DATA / "global_drops.json"
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return 0
    n = 0
    for iid, spec in (data or {}).items():
        if not str(iid).lstrip("-").isdigit():
            continue
        if not conn.execute("SELECT 1 FROM items WHERE item_id=?", (int(iid),)).fetchone():
            continue                                   # item not in catalog -> skip (FK)
        conn.execute(
            "INSERT INTO global_drops(item_id, rate, quantity) VALUES(?,?,?) "
            "ON CONFLICT(item_id) DO NOTHING",
            (int(iid), float((spec or {}).get("rate", 0.05) or 0.05),
             int((spec or {}).get("quantity", 1) or 1)))
        n += 1
    return n


def seed_item_sponsors(conn):
    """Item sponsorship credits from data/item_sponsors.json — {"<ItemID>": [{name, has_house}]}.
    Feeds the item_sponsors table the client's ItemPreviewNew panel reads via getItemSponsors
    (AE's Benevolent Founder credit). Idempotent (insert-if-absent); rows whose item isn't in the
    catalog are skipped (FK). Returns the number of sponsor rows seeded."""
    f = DATA / "item_sponsors.json"
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return 0
    n = 0
    for iid, sponsors in (data or {}).items():
        if not str(iid).lstrip("-").isdigit() or not isinstance(sponsors, list):
            continue                                   # skip "_comment"/doc keys
        if not conn.execute("SELECT 1 FROM items WHERE item_id=?", (int(iid),)).fetchone():
            continue                                   # item not in catalog -> skip (FK)
        for seq, s in enumerate(sponsors):
            name = (s or {}).get("name")
            if not name:
                continue
            conn.execute(
                "INSERT INTO item_sponsors(item_id, player_name, has_house, seq) VALUES(?,?,?,?) "
                "ON CONFLICT(item_id, player_name) DO NOTHING",
                (int(iid), str(name), 1 if (s or {}).get("has_house", True) else 0, seq))
            n += 1
    return n


def seed_quest_objective_refs(conn):
    """Authored, self-describing kill-credit mappings (+ probabilistic drop params) from
    data/quest_objective_refs.json, keyed by quest then objective:
        {"<questID>": {"<QOID>": {"monsters": [mon_id,...],
                                   "chance": f, "min": n, "max": n}}}
    monsters = the catalog ids that credit the objective (quest_objective_refs rows carry
    quest_id+qoid+mon_id); chance/min/max (optional, default 1/1/1 = deterministic +1) = the
    probabilistic drop roll (quest_objective_drops). Makes objective crediting a pure table lookup
    instead of RefID/name guessing. Idempotent (insert-if-absent)."""
    f = DATA / "quest_objective_refs.json"
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return 0
    n = 0
    for qid, objs in (data or {}).items():
        if not str(qid).lstrip("-").isdigit() or not isinstance(objs, dict):
            continue
        for qoid, spec in objs.items():
            if not str(qoid).lstrip("-").isdigit():
                continue
            mons = spec.get("monsters") if isinstance(spec, dict) else spec
            if isinstance(spec, dict) and any(k in spec for k in ("chance", "min", "max")):
                conn.execute(
                    "INSERT INTO quest_objective_drops(qoid, chance, min_qty, max_qty) "
                    "VALUES(?,?,?,?) ON CONFLICT(qoid) DO NOTHING",
                    (int(qoid), float(spec.get("chance", 1.0) or 1.0),
                     int(spec.get("min", 1) or 1), int(spec.get("max", 1) or 1)))
            for mid in (mons or []):
                conn.execute(
                    "INSERT INTO quest_objective_refs(quest_id, qoid, mon_id) VALUES(?,?,?) "
                    "ON CONFLICT(qoid, mon_id) DO NOTHING", (int(qid), int(qoid), int(mid)))
                n += 1
    return n


def run():
    db.init()
    with db.connect() as conn:
        # Catalog from versioned data/ files (exported from the live DB). Items first so
        hairs = seed_hairs(conn)
        # shop_items/links resolve.
        items = seed_items(conn)
        redeem_codes = seed_redeem_codes(conn)
        spellstones = seed_practice_spellstone(conn)
        shops, links = seed_shops(conn)
        dev_shop = fill_dev_shop.fill(conn)
        mons = montemplates.seed_db(conn)
        maps = seed_maps(conn)
        quests = seed_quests(conn)
        apops = seed_apops(conn)            # the apop catalog lives in the DB (editable on the fly,
                                            # like AE); CreateNewApop/DialoggerSave mutate it in place.
        cls, sk = seed_classes(conn)
        mon_skills = seed_monster_skills(conn)   # before seed_skill_graphs (it writes the version)
        graphs = seed_skill_graphs(conn)
        pala = seed_paladin(conn)               # Reduxidain Paladin (Conviction class)
        void = seed_void(conn)                  # Voidwalker (Hunger class, hidden Void anims)
        ihero = seed_infinity_hero(conn)        # Infinity Hero (class 2022, pure-data mechanics)
        chrono = seed_chronomancer(conn)        # Chronomancer (12-charge time-control class)
        class_grants = grant_class_items(conn)
        cutscenes = seed_cutscenes(conn)
        dclasses = seed_defaultclasses(conn)
        mdrops = seed_monster_drops(conn)
        gdrops = seed_global_drops(conn)
        qrefs = seed_quest_objective_refs(conn)
        isponsors = seed_item_sponsors(conn)
        conn.commit()
    print(f"[seed] items={items} spellstones={spellstones} hairs={hairs} shops={shops} shop_items={links} dev_shop_items={dev_shop} monsters={mons} maps={maps} "
          f"quests={quests} apops={apops} classes={cls} skills={sk} skill_graphs={graphs} "
          f"monster_skills_linked={mon_skills} paladin_skills={pala} void_skills={void} "
          f"infinity_hero_skills={ihero} "
          f"chronomancer_skills={chrono} "
          f"class_items_granted={class_grants} cutscenes={cutscenes} defaultclasses={dclasses} "
          f"monster_drops={mdrops} global_drops={gdrops} quest_obj_refs={qrefs} item_sponsors={isponsors} "
          f"redeem_codes={redeem_codes}")


if __name__ == "__main__":
    run()
