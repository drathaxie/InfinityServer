#!/usr/bin/env python3
"""
Load the harvested live-AE asset-bundle catalog (capture/harvest/bundles_catalog.json,
produced by harvest_catalog.py) into the asset_bundles table.

Upstream Artix metadata, not player data - safe to re-run any time to refresh the
catalog (ON CONFLICT DO UPDATE). Row shape from data/GetAssetBundlesByIDs:
    {ID, Name, Type, FileName, Version, VersionContent, VersionStage, VersionLive,
     DependencyID}

Usage:
    python capture/import_bundle_catalog.py [path/to/bundles_catalog.json]
"""
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "server"))
import db          # noqa: E402

DEFAULT_CATALOG = pathlib.Path(__file__).resolve().parent / "harvest" / "bundles_catalog.json"


def main():
    path = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_CATALOG
    if not path.exists():
        print(f"catalog not found: {path}", file=sys.stderr)
        sys.exit(1)

    rows = json.loads(path.read_text(encoding="utf-8"))
    print(f"loaded {len(rows)} rows from {path}")

    db.init()
    conn = db.connect()
    n = 0
    for r in rows:
        bid = r.get("ID")
        if bid is None:
            continue
        conn.execute(
            "INSERT INTO asset_bundles "
            "(bundle_id, name, type, filename, version_content, version_stage, "
            " version_live, dependency_id) "
            "VALUES (?,?,?,?,?,?,?,?) "
            "ON CONFLICT(bundle_id) DO UPDATE SET "
            "name=excluded.name, type=excluded.type, filename=excluded.filename, "
            "version_content=excluded.version_content, version_stage=excluded.version_stage, "
            "version_live=excluded.version_live, dependency_id=excluded.dependency_id",
            (bid, r.get("Name"), r.get("Type"),
             r.get("FileName") or r.get("Filename"),
             int(r.get("VersionContent") or 0), int(r.get("VersionStage") or 0),
             int(r.get("VersionLive") or 0), int(r.get("DependencyID") or 0)))
        n += 1
    conn.commit()
    print(f"upserted {n} asset_bundles rows")

    total = conn.execute("SELECT COUNT(*) AS c FROM asset_bundles").fetchone()["c"]
    unreleased = conn.execute(
        "SELECT COUNT(*) AS c FROM asset_bundles WHERE version_live=0 "
        "AND (version_stage>0 OR version_content>0)").fetchone()["c"]
    print(f"table now has {total} rows total, {unreleased} look unreleased "
          "(version_live=0, stage/content>0)")


if __name__ == "__main__":
    main()
