"""Generated, decoratable hero-statue house items and their PNG artwork."""
import json
import math
import struct
import time

import db

STATUE_ITEM_ID = 978659   # AE's real "Player KS Statue" (bundle 78659), live since 2026-07-31
STATUE_COOLDOWN_SECONDS = 300
_ELIGIBLE_BITS = tuple(range(6, 11))


def eligible(char):
    """All logged-in players may generate their custom hero statue.

    The old client-era gate checked Kickstarter/backer achievement bits. Vinchi is
    now public in Battleon, so server generation should be public too; cooldowns
    still limit normal accounts, while staff/dev accounts can bypass cooldowns.
    """
    return char is not None



def bypass_cooldown(char):
    """Dev/staff accounts can regenerate repeatedly while tuning the renderer."""
    try:
        return int(char["access_level"] or 0) >= 40
    except (KeyError, TypeError, ValueError):
        return False


def _item_definition(conn):
    # AE's real Player KS Statue (item 978659 / bundle 78659), which went live on
    # 2026-07-31. Was previously our fabricated item 200002 with Bundle:None + the
    # shipped-Resources prefab; now the genuine catalog item + real bundle. The
    # per-character art is still OUR rendered PNG (served by render_png / the mod's
    # DynamicStatue redirect) — AE's Statues CDN only has AE characters, not ours.
    # House flags are added on top of the imported catalog row so it's placeable.
    return {
        "ID": STATUE_ITEM_ID,
        "Name": "Player KS Statue",
        "Description": "A generated likeness of your hero that can be placed and decorated in your house.",
        "Cost": 0,
        "Quantity": 1,
        "StackSize": 1,
        "ItemType": 25,
        "EquipSpot": 9,
        "Level": 1,
        "Element": 1,
        "Faction": 1,
        "Icon": "ihfloor",
        "Coins": True,
        "House": True,
        "HouseInventory": True,
        "MobileCompatibility": 1,
        "PrefabName": "playerksstatue_houseItemGO",
        "Filename": "items/flooritems/78659_playerksstatue.unity3d",
        "Bundle": {
            "ID": 78659,
            "Name": "Player KS Statue",
            "Filename": "items/flooritems/78659_playerksstatue.unity3d",
            "VersionStage": 1,
            "VersionLive": 1,
        },
    }


def _snapshot(conn, char):
    equipped = []
    for row in conn.execute(
            "SELECT ci.item_id, i.name, i.item_type, i.equip_spot "
            "FROM char_items ci JOIN items i ON i.item_id=ci.item_id "
            "WHERE ci.char_id=? AND ci.equipped=1 ORDER BY i.equip_spot",
            (char["id"],)):
        equipped.append({
            "id": int(row["item_id"]),
            "name": row["name"] or "",
            "type": int(row["item_type"] or 0),
            "spot": int(row["equip_spot"] or 0),
        })
    return {
        "version": 1,
        "char_id": int(char["id"]),
        "name": char["name"] or "",
        "gender": char["gender"] or "",
        "hair_id": int(char["hair_id"] or 0),
        "skin": int(char["skin_color"] or 0) & 0xFFFFFF,
        "eye": int(char["eye_color"] or 0) & 0xFFFFFF,
        "hair": int(char["hair_color"] or 0) & 0xFFFFFF,
        "trim": int(char["trim_color"] or 0) & 0xFFFFFF,
        "accessory": int(char["accessory_color"] or 0) & 0xFFFFFF,
        "equipped": equipped,
    }


def generate(conn, char, now=None):
    """Create or refresh one non-stacking statue and return (response, houseItem)."""
    now = float(time.time() if now is None else now)
    if not eligible(char):
        return ({
            "Cmd": "generateStatue", "Success": False, "ItemID": 0,
            "Message": "You are not eligible to generate a statue.",
            "CooldownRemainingMs": 0,
        }, None)

    previous = conn.execute(
        "SELECT generated_at FROM statues WHERE char_id=?", (char["id"],)).fetchone()
    if previous is not None and not bypass_cooldown(char):
        remaining = STATUE_COOLDOWN_SECONDS - (now - float(previous["generated_at"]))
        if remaining > 0:
            return ({
                "Cmd": "generateStatue", "Success": False, "ItemID": STATUE_ITEM_ID,
                "Message": f"You can generate another statue in {math.ceil(remaining / 60)}m.",
                "CooldownRemainingMs": int(math.ceil(remaining * 1000)),
            }, None)

    item = _item_definition(conn)
    # replace=True also migrates the early implementation which accidentally
    # copied the Day 1 reward's bundle/prefab into this custom item definition.
    db.store_item(conn, item, replace=True)
    meta = f"custom:1,cid:{int(char['id'])},rev:{int(now)}"
    ci = conn.execute(
        "SELECT * FROM char_items WHERE char_id=? AND item_id=? ORDER BY char_item_id LIMIT 1",
        (char["id"], STATUE_ITEM_ID)).fetchone()
    created = ci is None
    if ci is None:
        import game
        char_item_id = game._next_char_item_id(conn)
        conn.execute(
            "INSERT INTO char_items(char_item_id,char_id,item_id,quantity,equipped,banked,"
            "loot_id,meta) VALUES(?,?,?,?,?,?,?,?)",
            (char_item_id, char["id"], STATUE_ITEM_ID, 1, 0, 0, -1, meta))
    else:
        char_item_id = int(ci["char_item_id"])
        conn.execute(
            "UPDATE char_items SET quantity=1,banked=0,meta=? WHERE char_item_id=?",
            (meta, char_item_id))

    snapshot = json.dumps(_snapshot(conn, char), separators=(",", ":"))
    conn.execute(
        "INSERT INTO statues(char_id,item_id,generated_at,snapshot,image) VALUES(?,?,?,?,NULL) "
        "ON CONFLICT(char_id) DO UPDATE SET item_id=excluded.item_id,"
        "generated_at=excluded.generated_at,snapshot=excluded.snapshot,image=NULL",
        (char["id"], STATUE_ITEM_ID, now, snapshot))
    ci = conn.execute(
        "SELECT * FROM char_items WHERE char_item_id=?", (char_item_id,)).fetchone()
    import game
    house_item = game._house_item_wire(conn, ci) if created else None
    return ({
        "Cmd": "generateStatue", "Success": True, "ItemID": STATUE_ITEM_ID,
        "Message": "Your character statue is rendering for your house-item inventory.",
        "CooldownRemainingMs": 0 if bypass_cooldown(char) else STATUE_COOLDOWN_SECONDS * 1000,
    }, house_item)


PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
MAX_RENDER_BYTES = 8 * 1024 * 1024


def validate_render_png(image):
    """Accept one bounded PNG; decoding remains Unity's responsibility."""
    if isinstance(image, memoryview):
        image = image.tobytes()
    if not isinstance(image, (bytes, bytearray)):
        return False
    if not (64 <= len(image) <= MAX_RENDER_BYTES):
        return False
    if image[:8] != PNG_SIGNATURE or image[12:16] != b"IHDR" or len(image) < 33:
        return False
    width, height = struct.unpack(">II", image[16:24])
    return 64 <= width <= 2048 and 64 <= height <= 2048


def store_render(conn, char_id, image):
    """Attach the authenticated client's real assembled-character render."""
    char_id = int(char_id)
    if not validate_render_png(image):
        return False
    exists = conn.execute(
        "SELECT 1 FROM statues s JOIN char_items ci ON ci.char_id=s.char_id "
        "AND ci.item_id=s.item_id WHERE s.char_id=? AND s.item_id=?",
        (char_id, STATUE_ITEM_ID)).fetchone()
    if exists is None:
        return False
    conn.execute("UPDATE statues SET image=? WHERE char_id=?", (bytes(image), char_id))
    return True


def store_render_force(conn, char_id, image):
    """Staff/batch path: store a render for ANY character, creating the statue row if it doesn't
    exist yet. Used by /genstatues (the FounderTower roster), which renders characters that never
    ran generateStatue themselves. Only the render (statues.image) is needed for the tower; the
    house char_item is left to the normal generate() flow."""
    char_id = int(char_id)
    if not validate_render_png(image):
        return False
    snapshot = json.dumps({"version": 1, "char_id": char_id, "source": "genstatues"},
                          separators=(",", ":"))
    conn.execute(
        "INSERT INTO statues(char_id,item_id,generated_at,snapshot,image) VALUES(?,?,?,?,?) "
        "ON CONFLICT(char_id) DO UPDATE SET image=excluded.image, generated_at=excluded.generated_at",
        (char_id, STATUE_ITEM_ID, float(time.time()), snapshot, bytes(image)))
    return True


def render_png(conn, char_id):
    """Return only the uploaded real-character render; never invent substitute art."""
    row = conn.execute("SELECT image FROM statues WHERE char_id=?", (int(char_id),)).fetchone()
    if row is None or row["image"] is None:
        return None
    image = row["image"]
    return image.tobytes() if isinstance(image, memoryview) else bytes(image)

