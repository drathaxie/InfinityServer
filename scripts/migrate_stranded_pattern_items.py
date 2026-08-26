#!/usr/bin/env python3
"""Move misfiled ItemType 43 gem tokens from char_items into char_patterns.

Dry-run is the default.  ``--apply`` requires ``--backup`` and writes a JSON manifest before
opening the transaction.  Each unit of quantity becomes one independently addressable
PatternItem, preserving the exact stored ItemPattern JSON where available.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "server"))

import db  # noqa: E402
import patterns  # noqa: E402


def stranded_rows(conn):
    return conn.execute(
        "SELECT ci.*, ch.name AS character_name, it.name AS item_name "
        "FROM char_items ci JOIN characters ch ON ch.id=ci.char_id "
        "JOIN items it ON it.item_id=ci.item_id "
        "WHERE it.item_type=? ORDER BY ci.char_id, ci.char_item_id",
        (patterns.ITEMTYPE_GEM,),
    ).fetchall()


def pattern_for(conn, row):
    if row["pattern_json"]:
        try:
            pat = json.loads(row["pattern_json"])
            if isinstance(pat, dict):
                return pat
        except (TypeError, ValueError):
            pass
    item = db.item(conn, row["item_id"]) or {}
    pat = item.get("ItemPattern")
    if isinstance(pat, dict):
        return pat
    return patterns.gem_item_pattern(item)


def manifest(conn, rows):
    data = []
    for row in rows:
        data.append({
            "char_item_id": int(row["char_item_id"]),
            "char_id": int(row["char_id"]),
            "character_name": row["character_name"],
            "item_id": int(row["item_id"]),
            "item_name": row["item_name"],
            "quantity": int(row["quantity"] or 1),
            "equipped": bool(row["equipped"]),
            "banked": bool(row["banked"]),
            "loot_id": int(row["loot_id"] or -1),
            "char_pattern_id": row["char_pattern_id"],
            "pattern": pattern_for(conn, row),
        })
    return data


def summary(data):
    return {
        "rows": len(data),
        "gems": sum(x["quantity"] for x in data),
        "characters": len({x["char_id"] for x in data}),
        "equipped_rows": sum(1 for x in data if x["equipped"]),
        "banked_rows": sum(1 for x in data if x["banked"]),
        "by_character": {
            name: sum(x["quantity"] for x in data if x["character_name"] == name)
            for name in sorted({x["character_name"] for x in data})
        },
    }


def apply_migration(conn, data):
    if any(x["equipped"] or x["banked"] for x in data):
        raise RuntimeError("refusing migration: a stranded gem row is equipped or banked")
    before = int(conn.execute("SELECT COUNT(*) AS n FROM char_patterns").fetchone()["n"])
    made = 0
    try:
        for entry in data:
            if not isinstance(entry["pattern"], dict):
                raise RuntimeError(f"no valid Pattern JSON for char_item {entry['char_item_id']}")
            for _ in range(entry["quantity"]):
                patterns.grant_gem(conn, entry["char_id"], entry["pattern"])
                made += 1
            conn.execute("DELETE FROM char_items WHERE char_item_id=?", (entry["char_item_id"],))

        remaining = int(conn.execute(
            "SELECT COUNT(*) AS n FROM char_items ci JOIN items it ON it.item_id=ci.item_id "
            "WHERE it.item_type=?", (patterns.ITEMTYPE_GEM,)).fetchone()["n"])
        after = int(conn.execute("SELECT COUNT(*) AS n FROM char_patterns").fetchone()["n"])
        if remaining != 0 or after - before != made:
            raise RuntimeError(
                f"postcondition failed: remaining={remaining}, pattern_delta={after-before}, made={made}"
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return {"inserted_patterns": made, "deleted_char_item_rows": len(data), "remaining": 0}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="commit the migration")
    ap.add_argument("--backup", type=pathlib.Path,
                    help="required with --apply; JSON manifest written before mutation")
    args = ap.parse_args()
    if args.apply and args.backup is None:
        ap.error("--apply requires --backup PATH")

    conn = db.connect()
    try:
        rows = stranded_rows(conn)
        data = manifest(conn, rows)
        report = summary(data)
        report["mode"] = "apply" if args.apply else "dry-run"
        print(json.dumps(report, indent=2, sort_keys=True))
        if not args.apply:
            return

        args.backup.parent.mkdir(parents=True, exist_ok=True)
        args.backup.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
        result = apply_migration(conn, data)
        print(json.dumps({"backup": str(args.backup), **result}, indent=2, sort_keys=True))
    finally:
        conn.close()


if __name__ == "__main__":
    main()
