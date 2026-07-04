#!/usr/bin/env python3
"""
TEST: insert the harvested Type=="ARMOR" bundles (capture/harvest/bundles_catalog.json) into
the items table as placeholder catalog entries, so they're equippable/spawnable content instead
of just a name+filename in a JSON blob.

We don't have real item defs for these (no live "item description by id" endpoint exists — see
[data-sources-audit] memory) so most fields are a TEMPLATE, not fabricated flavor text:
  - Name: derived from the bundle FileName (armors/49065_ArcanaArmor.unity3d -> "ArcanaArmor"),
    per instruction. NOT a real display name.
  - Description: left empty. Inventing flavor text would be fabricated data.
  - EquipSpot/ItemType/PrefabName/Icon/Linkage/StackSize/Level/Element/Faction/
    MobileCompatibility/DamageRange: from a REAL, 100%-consistent template, validated against
    all 170 harvested ARMOR bundles that already match a known item in data/items.json
    (EquipSpot=7, ItemType=22, PrefabName="ArmorSlots", Icon="iwarmor", DamageRange=0.1, ...).
  - Cost/Rarity/DPS/UpgradeOnly/Coins: real items vary here per-item; left unset (unknown) rather
    than guessed.
  - Bundle: the harvested {ID,Name,Filename,Version*} as-is, so the client can actually load art.

item_id: AE never assigned these a real catalog item_id (no item def endpoint to pull one from),
so we use a synthetic 900000+bundle_id range — clear of the real catalog (max known item_id is
~101344) and trivially reversible (item_id-900000 == bundle_id) so this test batch can be found
and dropped later if we don't want to keep it.

Usage:
    python capture/import_armor_items.py [path/to/bundles_catalog.json]
"""
import json
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "server"))
import db          # noqa: E402

DEFAULT_CATALOG = pathlib.Path(__file__).resolve().parent / "harvest" / "bundles_catalog.json"
SYNTHETIC_ID_BASE = 900000

# Validated against all 170 harvested ARMOR bundles that already match a known items.json entry.
TEMPLATE = {
    "EquipSpot": 7, "ItemType": 22, "PrefabName": "ArmorSlots", "Icon": "iwarmor",
    "Linkage": "", "StackSize": 1, "Level": 1, "Element": 1, "Faction": 1,
    "MobileCompatibility": 1, "DamageRange": 0.1, "ReqQuests": [], "strReqQuests": "",
    "MetaString": "", "boostValues": {},
}


def name_from_filename(filename):
    """armors/49065_ArcanaArmor.unity3d -> ArcanaArmor"""
    base = filename.rsplit("/", 1)[-1]
    stem = re.sub(r"\.unity3d$", "", base, flags=re.IGNORECASE)
    return re.sub(r"^\d+_", "", stem)


def main():
    path = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_CATALOG
    if not path.exists():
        sys.exit(f"catalog not found: {path}")

    cat = json.loads(path.read_text(encoding="utf-8"))
    armor_rows = [r for r in cat if r.get("Type") == "ARMOR"]
    print(f"{len(armor_rows)} ARMOR bundles in the harvested catalog")

    db.init()
    conn = db.connect()
    n = 0
    for r in armor_rows:
        bid = r.get("ID")
        fn = r.get("FileName") or r.get("Filename") or ""
        if bid is None or not fn:
            continue
        item_id = SYNTHETIC_ID_BASE + int(bid)
        it = dict(TEMPLATE)
        it["ID"] = item_id
        it["Name"] = name_from_filename(fn)
        it["Description"] = ""
        it["Filename"] = fn
        it["Bundle"] = {
            "ID": bid, "Name": r.get("Name"), "Filename": fn,
            "VersionStage": r.get("VersionStage"), "VersionLive": r.get("VersionLive"),
        }
        db.store_item(conn, it, replace=False)   # insert-if-absent: re-runs never clobber edits
        n += 1
    conn.commit()
    print(f"upserted {n} placeholder armor items (item_id = {SYNTHETIC_ID_BASE} + bundle_id)")

    total = conn.execute(
        "SELECT COUNT(*) AS c FROM items WHERE item_id >= ?", (SYNTHETIC_ID_BASE,)).fetchone()["c"]
    print(f"items table now has {total} rows in the synthetic test range (>= {SYNTHETIC_ID_BASE})")


if __name__ == "__main__":
    main()
