#!/usr/bin/env python3
r"""
Import every remaining harvested bundle type into the DB, into a sensible table per type,
comparing against what's already there so we don't duplicate real content:

  NPC, PET   -> monsters   (placeholder, synthetic mon_id; skip a bundle already used by a monster)
  CLASS      -> classes    (placeholder, synthetic class_id; skip a bundle already used by a class)
  MAP        -> maps        (placeholder, synthetic map_id; skip a map name we already have)
  (blank)    -> items       (consumables/potions — real Name in bundle; synthetic item_id)
  MUSIC/AUDIO-> soundtracks       (new flat catalog table)
  CUTSCENE   -> cutscene_bundles  (new flat table; cinematic ART, not the dialog `cutscenes` table)

Same conventions as the item/armor imports: synthetic ids are 900000+bundle_id (reversible,
clear of real ids), Name is the bundle's own Name (these types carry a real one, unlike the item
art bundles), Descriptions stay empty, insert-if-absent so re-runs never clobber real/edited rows.

Usage:
    python capture/import_remaining_bundles.py [path/to/bundles_catalog.json]
"""
import json
import pathlib
import re
import sys
from collections import Counter

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "server"))
import db            # noqa: E402
import montemplates  # noqa: E402

HERE = pathlib.Path(__file__).resolve().parent
DEFAULT_CATALOG = HERE / "harvest" / "bundles_catalog.json"
BASE = 900000


def name_from_filename(fn):
    base = fn.rsplit("/", 1)[-1]
    stem = re.sub(r"\.unity3d$", "", base, flags=re.IGNORECASE)
    return re.sub(r"^\d+_", "", stem)


def main():
    path = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_CATALOG
    if not path.exists():
        sys.exit(f"catalog not found: {path}")
    cat = json.loads(path.read_text(encoding="utf-8"))

    def rows_of(t):
        return [r for r in cat if r.get("Type") == t]

    def fn_of(r):
        return r.get("FileName") or r.get("Filename") or ""

    def bundle_obj(r):
        return {"ID": r.get("ID"), "Name": r.get("Name"), "Filename": fn_of(r),
                "VersionStage": r.get("VersionStage"), "VersionLive": r.get("VersionLive")}

    db.init()
    conn = db.connect()
    tally = Counter()

    # ---- NPC + PET -> monsters (skip art already used by a real monster) -------------
    used_mon_bundles = set()
    for row in conn.execute("SELECT bundle FROM monsters WHERE bundle IS NOT NULL"):
        try:
            f = (json.loads(row["bundle"]) or {}).get("Filename")
            if f:
                used_mon_bundles.add(f)
        except Exception:
            pass
    for r in rows_of("NPC") + rows_of("PET"):
        bid, fn = r.get("ID"), fn_of(r)
        if bid is None or not fn or fn in used_mon_bundles:
            tally["npc_skipped"] += 1
            continue
        mb = {"MonID": BASE + int(bid), "ID": BASE + int(bid),
              "strMonName": r.get("Name") or name_from_filename(fn),
              "Bundle": bundle_obj(r), "intHP": 100, "intHPMax": 100, "Level": 1,
              "equippedItems": {}}
        montemplates.store(conn, BASE + int(bid), mb)   # insert-if-absent
        tally["npc"] += 1

    # ---- CLASS -> classes (skip art already used by a class) -------------------------
    used_cls_bundles = set()
    for row in conn.execute("SELECT bundle FROM classes WHERE bundle IS NOT NULL"):
        try:
            f = (json.loads(row["bundle"]) or {}).get("Filename")
            if f:
                used_cls_bundles.add(f)
        except Exception:
            pass
    for r in rows_of("CLASS"):
        bid, fn = r.get("ID"), fn_of(r)
        if bid is None or not fn or fn in used_cls_bundles:
            tally["class_skipped"] += 1
            continue
        conn.execute(
            "INSERT INTO classes(class_id, name, bundle) VALUES(?,?,?) "
            "ON CONFLICT(class_id) DO NOTHING",
            (BASE + int(bid), r.get("Name") or name_from_filename(fn),
             json.dumps(bundle_obj(r), separators=(",", ":"))))
        tally["class"] += 1

    # ---- MAP -> maps (skip a map name we already have) -------------------------------
    have_map_names = set()
    for row in conn.execute("SELECT LOWER(str_map_name) n FROM maps WHERE str_map_name IS NOT NULL"):
        have_map_names.add(row["n"])
    for r in rows_of("MAP"):
        bid, fn = r.get("ID"), fn_of(r)
        nm = (r.get("Name") or name_from_filename(fn))
        if bid is None or not fn or nm.lower() in have_map_names:
            tally["map_skipped"] += 1
            continue
        conn.execute(
            "INSERT INTO maps(map_id, area_name, str_map_name, display_name, bundle, raw) "
            "VALUES(?,?,?,?,?,?) ON CONFLICT(map_id) DO NOTHING",
            (BASE + int(bid), nm, nm, nm,
             json.dumps(bundle_obj(r), separators=(",", ":")), "{}"))
        have_map_names.add(nm.lower())
        tally["map"] += 1

    # ---- (blank) consumables -> items ------------------------------------------------
    for r in rows_of(""):
        bid, fn = r.get("ID"), fn_of(r)
        if bid is None:
            continue
        # filename tail is the icon code (e.g. "2511_icm1" -> "icm1")
        icon = re.sub(r"^\d+_", "", fn.rsplit("/", 1)[-1]) or "icm1"
        it = {"ID": BASE + int(bid), "Name": r.get("Name") or name_from_filename(fn),
              "Description": "", "ItemType": 4, "EquipSpot": 0, "Icon": icon,
              "StackSize": 100, "Level": 1, "Filename": fn, "Bundle": bundle_obj(r),
              "ReqQuests": [], "boostValues": {}}
        db.store_item(conn, it, replace=False)
        tally["consumable"] += 1

    # ---- MUSIC + AUDIO -> soundtracks -------------------------------------------------
    for r in rows_of("MUSIC") + rows_of("AUDIO"):
        bid = r.get("ID")
        if bid is None:
            continue
        conn.execute(
            "INSERT INTO soundtracks(bundle_id, name, kind, filename, version_stage, version_live) "
            "VALUES(?,?,?,?,?,?) ON CONFLICT(bundle_id) DO NOTHING",
            (int(bid), r.get("Name"), r.get("Type"), fn_of(r),
             int(r.get("VersionStage") or 0), int(r.get("VersionLive") or 0)))
        tally["soundtrack"] += 1

    # ---- CUTSCENE -> cutscene_bundles -------------------------------------------------
    for r in rows_of("CUTSCENE"):
        bid = r.get("ID")
        if bid is None:
            continue
        conn.execute(
            "INSERT INTO cutscene_bundles(bundle_id, name, filename, version_stage, version_live) "
            "VALUES(?,?,?,?,?) ON CONFLICT(bundle_id) DO NOTHING",
            (int(bid), r.get("Name"), fn_of(r),
             int(r.get("VersionStage") or 0), int(r.get("VersionLive") or 0)))
        tally["cutscene"] += 1

    conn.commit()
    print("inserted / skipped:")
    for k in sorted(tally):
        print(f"  {k:16} {tally[k]}")
    for tbl in ("monsters", "classes", "maps", "soundtracks", "cutscene_bundles"):
        c = conn.execute(f"SELECT COUNT(*) AS c FROM {tbl}").fetchone()["c"]
        print(f"  {tbl:16} now {c} rows")


if __name__ == "__main__":
    main()
