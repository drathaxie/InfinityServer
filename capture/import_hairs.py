#!/usr/bin/env python3
"""
Populate the hairs table from the harvested asset catalog (Type=="HAIR", 576 bundles) plus the
24 real hairs AE's Data/GetBaseClasses exposes (capture/harvest/baseclasses_live.json).

Two ID sources, same as the armor items test (see import_armor_items.py):
  - 24 hairs have a REAL hair_id (the numeric ID char-create/charedit actually sends), known
    because they're the default hairs the live base_classes.hairs list carries.
  - The other ~552 HAIR bundles have no such catalog id anywhere reachable (no GetHairsByIDs
    endpoint exists) — same synthetic 900000+bundle_id range used for the armor items, so it's
    clear which rows are placeholder ids vs AE-real ones.

Name/Gender are NOT fabricated: both come straight off the real bundle filename, which is a
confirmed, 100%-consistent pattern across all 576 rows: hair/<Gender>/<id>_<Name>_<Gender>.unity3d
(e.g. hair/F/45925_Bangs1_F.unity3d -> Name="Bangs1", Gender="F").

Usage:
    python capture/import_hairs.py [path/to/bundles_catalog.json] [path/to/baseclasses_live.json]
"""
import json
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "server"))
import db          # noqa: E402

HERE = pathlib.Path(__file__).resolve().parent
DEFAULT_CATALOG = HERE / "harvest" / "bundles_catalog.json"
DEFAULT_BASECLASSES = HERE / "harvest" / "baseclasses_live.json"
SYNTHETIC_ID_BASE = 900000

FN_RE = re.compile(r"^hair/([MF])/\d+_(.+)_([MF])\.unity3d$", re.IGNORECASE)


def name_gender_from_filename(filename):
    """hair/F/45925_Bangs1_F.unity3d -> ("Bangs1", "F"). Falls back to the path's gender segment
    and a plain de-prefixed stem if a filename doesn't match the id_Name_Gender tail exactly."""
    m = FN_RE.match(filename)
    if m:
        return m.group(2), m.group(1).upper()
    path_gender = filename.split("/")[1].upper() if filename.lower().startswith("hair/") else None
    stem = filename.rsplit("/", 1)[-1]
    stem = re.sub(r"\.unity3d$", "", stem, flags=re.IGNORECASE)
    stem = re.sub(r"^\d+_", "", stem)
    return stem, path_gender


def main():
    catalog_path = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_CATALOG
    base_path = pathlib.Path(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_BASECLASSES
    if not catalog_path.exists():
        sys.exit(f"catalog not found: {catalog_path}")

    cat = json.loads(catalog_path.read_text(encoding="utf-8"))
    hair_bundles = [r for r in cat if r.get("Type") == "HAIR"]
    print(f"{len(hair_bundles)} HAIR bundles in the harvested catalog")

    known_by_bundle_id = {}
    if base_path.exists():
        base = json.loads(base_path.read_text(encoding="utf-8"))
        for h in base.get("hairs") or []:
            b = h.get("Bundle") or {}
            if b.get("ID") is not None:
                known_by_bundle_id[int(b["ID"])] = h
    print(f"{len(known_by_bundle_id)} known real hair_ids from GetBaseClasses")

    db.init()
    conn = db.connect()
    n_real = n_synth = 0
    for r in hair_bundles:
        bid = r.get("ID")
        fn = r.get("FileName") or r.get("Filename") or ""
        if bid is None or not fn:
            continue
        known = known_by_bundle_id.get(bid)
        if known:
            h = dict(known)          # real ID/Name/Gender/Filename/Bundle, as AE serves it
            n_real += 1
        else:
            name, gender = name_gender_from_filename(fn)
            h = {"ID": SYNTHETIC_ID_BASE + int(bid), "Name": name, "Gender": gender,
                 "Filename": fn,
                 "Bundle": {"ID": bid, "Name": r.get("Name"), "Filename": fn,
                            "VersionStage": r.get("VersionStage"),
                            "VersionLive": r.get("VersionLive")}}
            n_synth += 1
        db.store_hair(conn, h, replace=False)   # insert-if-absent: re-runs never clobber edits
    conn.commit()
    print(f"upserted {n_real} real-id + {n_synth} synthetic-id hairs "
          f"({n_real + n_synth} total)")

    total = conn.execute("SELECT COUNT(*) AS c FROM hairs").fetchone()["c"]
    by_gender = conn.execute(
        "SELECT gender, COUNT(*) AS c FROM hairs GROUP BY gender ORDER BY gender").fetchall()
    print(f"hairs table now has {total} rows: "
          + ", ".join(f"{r['gender']}={r['c']}" for r in by_gender))


if __name__ == "__main__":
    main()
