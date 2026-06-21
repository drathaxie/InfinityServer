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
import montemplates

DATA = pathlib.Path(__file__).resolve().parent.parent / "data"
MAPS_DIR = DATA / "maps"
QUESTS_FILE = DATA / "quests.json"
APOPS_FILE = DATA / "apops.json"
CUTSCENES_FILE = DATA / "cutscenes.json"
DEFAULTCLASSES_FILE = DATA / "defaultclasses.json"
MONSTER_DROPS_FILE = DATA / "monster_drops.json"
ITEMS_FILE = DATA / "items.json"
SHOPS_FILE = DATA / "shops.json"


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

    meta = json.loads(json.dumps(shop_obj))          # deep copy, items stripped
    (meta["shop"] if has_wrapper else meta)["items"] = []
    conn.execute(
        "INSERT INTO shops(shop_id, raw) VALUES(?, ?) "
        "ON CONFLICT(shop_id) DO NOTHING",
        (shop_id, json.dumps(meta, separators=(",", ":"))),
    )

    n = 0
    for it in items:
        item_id = int(it.get("ID", 0))
        conn.execute(
            "INSERT INTO items(item_id, name, item_type, raw) VALUES(?,?,?,?) "
            "ON CONFLICT(item_id) DO NOTHING",
            (item_id, it.get("Name"), int(it.get("ItemType", 0) or 0),
             json.dumps(db.item_template(it), separators=(",", ":"))),
        )
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
    conn.execute("DELETE FROM quest_turnins WHERE quest_id=?", (qid,))
    for i, t in enumerate(q.get("turnin") or []):
        conn.execute(
            "INSERT INTO quest_turnins(quest_id, idx, type, qo_type, qo_id, item_id, "
            "quantity, ref_ids) VALUES(?,?,?,?,?,?,?,?)",
            (qid, i, (t.get("$type", "").split(".")[-1].split(",")[0] or None),
             t.get("QOType"), t.get("QOID"), t.get("ItemID"), t.get("Quantity"),
             json.dumps(t.get("RefIDs")) if t.get("RefIDs") is not None else None),
        )
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
                cat = db.item_template(real)
                cat["Quantity"] = 1
                conn.execute(
                    "INSERT INTO items(item_id, name, item_type, raw) VALUES(?,?,?,?) "
                    "ON CONFLICT(item_id) DO NOTHING",
                    (iid, real.get("Name") or real.get("sName") or name,
                     int(real.get("ItemType", 21) or 21),
                     json.dumps(cat, separators=(",", ":"))))
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
                "auto_h_range, auto_v_range, mana) VALUES(?,?,?,?,?,?,?,?,?)",
                (sid, int(meta.get("act", 0) or 0), meta.get("nam"), meta.get("desc"),
                 meta.get("icon"), slot, float(meta.get("autoHRange", 0) or 0),
                 float(meta.get("autoVRange", 0) or 0), int(meta.get("regMana", 0) or 0)))
            n_skills += 1
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
SKILL_GRAPH_VERSION = 6


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
SKILL_GRAPHS_FILE = DATA / "skill_graphs.json"   # mined per-skill graphs (all classes)
CLASS_ITEM_DEFS_FILE = DATA / "class_item_defs.json"   # real captured class-armor item defs
_class_item_defs = {}

# Class items are ranked by class POINTS held in the owned instance's Quantity (InventoryItem.cs:
# `classRank = new Rank(Quantity)`; Inventory.hasClassPoints gates skills on `Quantity >= points`).
# Rank.cs caps at 302500 (max rank). We grant the MAX so every class is immediately playable —
# class-point PROGRESSION (earning CP to rank up) is a future mechanic; until then a consistent
# maxed CP is the faithful, non-corrupt state (vs the live 1 / 302499 / 302500 split). (P2-1)
CLASS_CP_MAX = 302500


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
            conn.execute("UPDATE skills SET data=?, forge_data=? WHERE skill_id=?",
                         (json.dumps(g_data, separators=(",", ":")),
                          json.dumps(g["forge"], separators=(",", ":")), sid))
            n += 1

    conn.execute("INSERT INTO kv(k,v) VALUES('skill_graph_version',?) "
                 "ON CONFLICT(k) DO UPDATE SET v=excluded.v", (str(SKILL_GRAPH_VERSION),))
    return n


def grant_class_items(conn):
    """Give every character the base class armors (from the seeded class items) so they can
    equip + play each class. Idempotent: only grants a class item a character doesn't own.
    Also reconciles class POINTS to a consistent maxed CP (P2-1) — the live DB had a corrupt
    1 / 302499 / 302500 split (grant default / sell-bug decrement / maxed)."""
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
    granted = 0
    for ch in conn.execute("SELECT id FROM characters").fetchall():
        for iid in item_ids:
            owned = conn.execute("SELECT 1 FROM char_items WHERE char_id=? AND item_id=?",
                                 (ch["id"], iid)).fetchone()
            if not owned:
                cid = int(db.kv_get(conn, "next_char_item_id", "1"))   # same counter as _grant_item
                db.kv_set(conn, "next_char_item_id", cid + 1)
                conn.execute("INSERT INTO char_items(char_item_id, char_id, item_id, quantity, "
                             "equipped, banked, loot_id) VALUES(?,?,?,?,0,0,-1)",
                             (cid, ch["id"], iid, CLASS_CP_MAX))   # grant maxed class points (CP)
                granted += 1
    return granted


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
        conn.execute(
            "INSERT INTO items(item_id, name, item_type, raw) VALUES(?,?,?,?) "
            "ON CONFLICT(item_id) DO NOTHING",
            (int(iid), it.get("Name"), int(it.get("ItemType", 0) or 0),
             json.dumps(it, separators=(",", ":"))))
    return len(items)


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
        conn.execute(
            "INSERT INTO shops(shop_id, raw) VALUES(?,?) ON CONFLICT(shop_id) DO NOTHING",
            (int(sid), json.dumps(s.get("meta", {}), separators=(",", ":"))))
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
    (every monster then uses loot.py's global pool). Returns the number of (mon,item) rows seeded."""
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


def run():
    db.init()
    with db.connect() as conn:
        # Catalog from versioned data/ files (exported from the live DB). Items first so
        # shop_items/links resolve.
        items = seed_items(conn)
        shops, links = seed_shops(conn)
        mons = montemplates.seed_db(conn)
        maps = seed_maps(conn)
        quests = seed_quests(conn)
        apops = seed_apops(conn)            # the apop catalog lives in the DB (editable on the fly,
                                            # like AE); CreateNewApop/DialoggerSave mutate it in place.
        cls, sk = seed_classes(conn)
        graphs = seed_skill_graphs(conn)
        class_grants = grant_class_items(conn)
        cutscenes = seed_cutscenes(conn)
        dclasses = seed_defaultclasses(conn)
        mdrops = seed_monster_drops(conn)
        conn.commit()
    print(f"[seed] items={items} shops={shops} shop_items={links} monsters={mons} maps={maps} "
          f"quests={quests} apops={apops} classes={cls} skills={sk} skill_graphs={graphs} "
          f"class_items_granted={class_grants} cutscenes={cutscenes} defaultclasses={dclasses} "
          f"monster_drops={mdrops}")


if __name__ == "__main__":
    run()
