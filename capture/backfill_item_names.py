#!/usr/bin/env python3
r"""
Backfill placeholder item Names (item_id >= 900000) from the harvested catalog's own Name
field, when that field looks like a real display name rather than an internal asset codename.

Every placeholder row already carries the catalog's Name inside its `bundle` JSON column (it
was captured but never promoted — import_item_bundles.py/import_remaining_bundles.py derive the
top-level Name from the FILENAME instead, since a raw sweep can't tell "real" from "codename").

Calibrated against the 1,592 items we have confirmed real names for (data/items.json, matched
by Bundle.ID): the catalog Name field matches the true display name 63% of the time overall,
but accuracy swings hard by ID range (74-88% in most bands, ~1-2% in the 50000-69999 band, where
Name is almost always the internal asset codename instead). A simple heuristic recovers most of
the signal without the noise: catalog names that CONTAIN A SPACE are real display names 83.7% of
the time and capture 95.1% of all recoverable real names; codenames are near-universally a single
CamelCase/underscore token with no space. So: promote only when the catalog Name has a space.

This is still placeholder-tier data (~1 in 6 promoted names will be an adjacent variant, e.g. the
base item's name when the owned bundle is actually a "Doom"/recolor tier of it) — same caveat as
the rest of the synthetic 900000+ range: replace with real defs when they arrive.

Usage:
    python capture/backfill_item_names.py
"""
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "server"))
import db  # noqa: E402

BASE = 900000


def main():
    db.init()
    conn = db.connect()
    rows = conn.execute(
        "SELECT item_id, name, bundle FROM items WHERE item_id >= ? AND bundle IS NOT NULL",
        (BASE,)).fetchall()

    updated = 0
    updates = []
    for r in rows:
        try:
            bundle = json.loads(r["bundle"] or "{}")
        except (TypeError, ValueError):
            continue
        catname = (bundle.get("Name") or "").strip()
        if not catname or " " not in catname:
            continue
        if catname == r["name"]:
            continue
        updates.append((catname, r["item_id"]))

    conn.executemany("UPDATE items SET name = ? WHERE item_id = ?", updates)
    conn.commit()
    updated = len(updates)
    print(f"{len(rows)} placeholder rows scanned, {updated} names promoted from catalog data")
    for catname, item_id in updates[:15]:
        print(f"  {item_id}: -> {catname!r}")
    if updated > 15:
        print(f"  ... and {updated - 15} more")


if __name__ == "__main__":
    main()
