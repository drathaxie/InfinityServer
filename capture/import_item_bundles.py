#!/usr/bin/env python3
r"""
Import the harvested Type=="ITEM" bundles (weapons, helms, capes, house items, ...) into the
items table as placeholder catalog entries — the same treatment the ARMOR bundles already got
(see import_armor_items.py), extended with a per-equipment-slot template.

Real item defs (Name + Description + stats) aren't reachable in bulk (no REST item endpoint;
socket item catalog is access-gated — see [data-sources-audit]). So most fields are a TEMPLATE,
not fabricated data:
  - Name        : derived from the bundle FileName (items/swords/49_BigAssBall.unity3d ->
                  "BigAssBall"). NOT a real display name.
  - Description : left empty (inventing flavor text = fabrication).
  - EquipSpot / ItemType / Icon : per-slot, taken from a template validated to be 100%
                  consistent (EquipSpot/Icon) or dominant (ItemType) across the known items in
                  data/items.json that share that folder.
  - PrefabName  : derived <assetName>+<slot suffix> (96% match vs known items; the misses are a
                  _weaponGO/_weaponSlots rig nuance the real def will correct).
  - Bundle      : the harvested {ID,Name,Filename,Version*} as-is, so art can load.

item_id: 900000 + bundle_id (same synthetic scheme as the armor test) — clear of the real
catalog (max real item_id ~101344; bundle ids max ~78k) and reversible (item_id-900000 ==
bundle_id) so this placeholder batch can be found/replaced when real defs arrive.

Usage:
    python capture/import_item_bundles.py [path/to/bundles_catalog.json]
"""
import json
import pathlib
import re
import sys
from collections import Counter

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "server"))
import db          # noqa: E402

HERE = pathlib.Path(__file__).resolve().parent
DEFAULT_CATALOG = HERE / "harvest" / "bundles_catalog.json"
SYNTHETIC_ID_BASE = 900000

# Per-slot template. equip_spot/item_type/icon derived from the known items sharing each folder
# (EquipSpot & Icon are 100% consistent; ItemType is the dominant value). prefab: how PrefabName
# is built — "armor" => constant "ArmorSlots"; else <assetName> + the given suffix.
SLOTS = {
    "helms":      dict(equip_spot=3, item_type=12, icon="iihelm",    prefab="armor"),
    "capes":      dict(equip_spot=4, item_type=15, icon="iicape",    prefab="_capeSlots"),
    "swords":     dict(equip_spot=2, item_type=6,  icon="iwsword",   prefab="_weaponSlots"),
    "maces":      dict(equip_spot=2, item_type=9,  icon="iwmace",    prefab="_weaponSlots"),
    "daggers":    dict(equip_spot=2, item_type=7,  icon="iwdagger",  prefab="_weaponSlots"),
    "polearms":   dict(equip_spot=2, item_type=20, icon="iwpolearm", prefab="_weaponSlots"),
    "staves":     dict(equip_spot=2, item_type=10, icon="iwstaff",   prefab="_weaponSlots"),
    "axes":       dict(equip_spot=2, item_type=8,  icon="iwaxe",     prefab="_weaponSlots"),
    "guns":       dict(equip_spot=2, item_type=19, icon="iwgun",     prefab="_weaponSlots"),
    "bows":       dict(equip_spot=2, item_type=30, icon="iwbow",     prefab="_weaponSlots"),
    "scythes":    dict(equip_spot=2, item_type=20, icon="iwpolearm", prefab="_weaponSlots"),
    "rifles":     dict(equip_spot=2, item_type=19, icon="iwgun",     prefab="_weaponSlots"),
    "wands":      dict(equip_spot=2, item_type=10, icon="iwstaff",   prefab="_weaponSlots"),
    "whips":      dict(equip_spot=2, item_type=31, icon="iwgun",     prefab="_weaponGO"),
    "handguns":   dict(equip_spot=2, item_type=33, icon="iwgun",     prefab="_weaponGO"),
    "gauntlets":  dict(equip_spot=2, item_type=32, icon="iwclaws",   prefab="_weaponGO"),
    "flooritems": dict(equip_spot=9, item_type=25, icon="ihfloor",   prefab="_houseItemGO"),
    "wallitems":  dict(equip_spot=9, item_type=24, icon="ihwall",    prefab="_houseItemGO"),
    "pets":       dict(equip_spot=5, item_type=18, icon="iipet",     prefab="_weaponSlots"),
}
# armor-path bundles typed ITEM (armor art) get the armor template (import_armor_items.py).
ARMOR_TMPL = dict(equip_spot=7, item_type=22, icon="iwarmor", prefab="armor")
# last-resort for a path we can't slot at all: a generic, non-equippable placeholder.
GENERIC_TMPL = dict(equip_spot=0, item_type=0, icon="iwsword", prefab="_weaponSlots")

COMMON = {
    "Linkage": "", "StackSize": 1, "Level": 1, "Element": 1, "Faction": 1,
    "MobileCompatibility": 1, "DamageRange": 0.1, "ReqQuests": [], "strReqQuests": "",
    "MetaString": "", "boostValues": {},
}


def name_from_filename(filename):
    base = filename.rsplit("/", 1)[-1]
    stem = re.sub(r"\.unity3d$", "", base, flags=re.IGNORECASE)
    return re.sub(r"^\d+_", "", stem)


def slot_for(filename):
    """(template, folder_label) for a bundle FileName."""
    m = re.match(r"items/([^/]+)/", filename or "", re.IGNORECASE)
    if m and m.group(1).lower() in SLOTS:
        f = m.group(1).lower()
        return SLOTS[f], f
    # loose: a known slot keyword anywhere in the path (handles Items/, missing slash, etc.)
    low = (filename or "").lower()
    for f, tmpl in SLOTS.items():
        if f"/{f}/" in low or re.search(r"[/_]" + f[:-1] + r"s?[/_0-9]", low):
            return tmpl, f
    if low.startswith("armors/"):
        return ARMOR_TMPL, "armor"
    return GENERIC_TMPL, "generic"


def prefab_name(filename, tmpl):
    if tmpl["prefab"] == "armor":
        return "ArmorSlots"
    return name_from_filename(filename) + tmpl["prefab"]


def main():
    path = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_CATALOG
    if not path.exists():
        sys.exit(f"catalog not found: {path}")

    cat = json.loads(path.read_text(encoding="utf-8"))
    item_rows = [r for r in cat if r.get("Type") == "ITEM"]
    print(f"{len(item_rows)} ITEM bundles in the harvested catalog")

    db.init()
    conn = db.connect()
    n = 0
    byslot = Counter()
    for r in item_rows:
        bid = r.get("ID")
        fn = r.get("FileName") or r.get("Filename") or ""
        if bid is None or not fn:
            continue
        tmpl, label = slot_for(fn)
        byslot[label] += 1
        it = dict(COMMON)
        it["ID"] = SYNTHETIC_ID_BASE + int(bid)
        it["Name"] = name_from_filename(fn)
        it["Description"] = ""
        it["Filename"] = fn
        it["EquipSpot"] = tmpl["equip_spot"]
        it["ItemType"] = tmpl["item_type"]
        it["Icon"] = tmpl["icon"]
        it["PrefabName"] = prefab_name(fn, tmpl)
        it["Bundle"] = {"ID": bid, "Name": r.get("Name"), "Filename": fn,
                        "VersionStage": r.get("VersionStage"), "VersionLive": r.get("VersionLive")}
        db.store_item(conn, it, replace=False)   # insert-if-absent: never clobber real/edited rows
        n += 1
    conn.commit()
    print(f"upserted {n} placeholder item rows (item_id = {SYNTHETIC_ID_BASE} + bundle_id)")
    print("by slot:", dict(byslot.most_common()))

    total = conn.execute(
        "SELECT COUNT(*) AS c FROM items WHERE item_id >= ?", (SYNTHETIC_ID_BASE,)).fetchone()["c"]
    print(f"items table now has {total} rows in the synthetic range (>= {SYNTHETIC_ID_BASE})")


if __name__ == "__main__":
    main()
