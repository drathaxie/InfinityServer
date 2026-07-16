#!/usr/bin/env python3
r"""
Import harvested bundle ITEM rows into the live catalog, overlaying AQW Wiki
name/description matches where available.

This is re-runnable. It first inserts every bundle as a synthetic placeholder
(item_id = 900000 + bundle_id), then replaces rows that have wiki matches with
wiki-backed Name/Description while preserving bundle filename/template fields.

Usage:
    python capture/import_wiki_item_matches.py \
        --catalog capture/harvest/bundles_item.json \
        --matches capture/harvest/aqw_wiki_item_matches*.jsonl
"""
import argparse
import glob
import json
import pathlib
import sys
from collections import Counter

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "server"))
import db  # noqa: E402

HERE = pathlib.Path(__file__).resolve().parent
DEFAULT_CATALOG = HERE / "harvest" / "bundles_item.json"
DEFAULT_MATCHES = str(HERE / "harvest" / "aqw_wiki_item_matches*.jsonl")
SYNTHETIC_ID_BASE = 900000

# Keep this in sync with import_item_bundles.py.
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
ARMOR_TMPL = dict(equip_spot=7, item_type=22, icon="iwarmor", prefab="armor")
GENERIC_TMPL = dict(equip_spot=0, item_type=0, icon="iwsword", prefab="_weaponSlots")
COMMON = {
    "Linkage": "", "StackSize": 1, "Level": 1, "Element": 1, "Faction": 1,
    "MobileCompatibility": 1, "DamageRange": 0.1, "ReqQuests": [], "strReqQuests": "",
    "MetaString": "", "boostValues": {},
}


def name_from_filename(filename):
    import re
    base = filename.rsplit("/", 1)[-1]
    stem = re.sub(r"\.unity3d$", "", base, flags=re.IGNORECASE)
    return re.sub(r"^\d+_", "", stem)


def slot_for(filename):
    import re
    m = re.match(r"items/([^/]+)/", filename or "", re.IGNORECASE)
    if m and m.group(1).lower() in SLOTS:
        f = m.group(1).lower()
        return SLOTS[f], f
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


def read_matches(patterns, min_score):
    files = []
    for pattern in patterns:
        files.extend(glob.glob(pattern))
    matches = {}
    for path in sorted(set(files)):
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                score = float(row.get("score") or 0)
                if score < min_score:
                    continue
                bid = int(row.get("item_id") or 0)
                old = matches.get(bid)
                if old is None or score > float(old.get("score") or 0):
                    matches[bid] = row
    return matches, sorted(set(files))


def item_from_bundle(row, match=None):
    bid = int(row["ID"])
    filename = row.get("FileName") or row.get("Filename") or ""
    tmpl, _label = slot_for(filename)
    it = dict(COMMON)
    it["ID"] = SYNTHETIC_ID_BASE + bid
    it["Name"] = (match or {}).get("wiki_name") or name_from_filename(filename)
    it["Description"] = (match or {}).get("wiki_description") or ""
    it["Filename"] = filename
    it["EquipSpot"] = tmpl["equip_spot"]
    it["ItemType"] = tmpl["item_type"]
    it["Icon"] = tmpl["icon"]
    it["PrefabName"] = prefab_name(filename, tmpl)
    it["Bundle"] = {
        "ID": bid,
        "Name": row.get("Name"),
        "Filename": filename,
        "VersionStage": row.get("VersionStage"),
        "VersionLive": row.get("VersionLive"),
    }
    return it


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--catalog", default=str(DEFAULT_CATALOG))
    ap.add_argument("--matches", action="append", default=[DEFAULT_MATCHES],
                    help="match report glob/path; repeatable")
    ap.add_argument("--min-score", type=float, default=0.92)
    ap.add_argument("--no-placeholders", action="store_true",
                    help="only import rows with wiki matches")
    args = ap.parse_args()

    catalog_path = pathlib.Path(args.catalog)
    rows = json.loads(catalog_path.read_text(encoding="utf-8"))
    rows = [r for r in rows if isinstance(r, dict) and r.get("ID") is not None]
    matches, match_files = read_matches(args.matches, args.min_score)

    db.init()
    conn = db.connect()
    inserted_or_seen = wiki = 0
    byslot = Counter()
    for row in rows:
        bid = int(row["ID"])
        if args.no_placeholders and bid not in matches:
            continue
        filename = row.get("FileName") or row.get("Filename") or ""
        _tmpl, label = slot_for(filename)
        byslot[label] += 1
        match = matches.get(bid)
        db.store_item(conn, item_from_bundle(row, match), replace=bool(match))
        inserted_or_seen += 1
        if match:
            wiki += 1
    conn.commit()

    total_synth = conn.execute(
        "SELECT COUNT(*) AS c FROM items WHERE item_id >= ?", (SYNTHETIC_ID_BASE,)
    ).fetchone()["c"]
    wiki_desc = conn.execute(
        "SELECT COUNT(*) AS c FROM items WHERE item_id >= ? AND description IS NOT NULL "
        "AND trim(description) <> ''", (SYNTHETIC_ID_BASE,)
    ).fetchone()["c"]
    print(f"[wiki-import] catalog: {catalog_path} ({len(rows)} bundle rows)")
    print(f"[wiki-import] matches: {len(matches)} rows from {len(match_files)} files")
    for path in match_files:
        print(f"  - {path}")
    print(f"[wiki-import] processed {inserted_or_seen} bundle rows; wiki overlays {wiki}")
    print(f"[wiki-import] synthetic catalog now has {total_synth} rows; {wiki_desc} with descriptions")
    print("[wiki-import] by slot:", dict(byslot.most_common()))


if __name__ == "__main__":
    main()
