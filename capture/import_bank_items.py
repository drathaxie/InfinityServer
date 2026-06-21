#!/usr/bin/env python3
"""
Ingest item definitions from captured loadBank packets into the LIVE catalog.

A bank packet carries hundreds of full InventoryItem defs (the player's stored
items). We don't import the *ownership* (the bank is per-player) — only the item
*definitions*, deduped into the shared `items` table via db.item_template (the
same normalize path shops use). Non-destructive: ON CONFLICT DO NOTHING, so it
only adds defs we don't already have and never rewrites an existing row.

Usage:
    python capture/import_bank_items.py [path/to/packets.jsonl]
"""
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "server"))
import db          # noqa: E402

DEFAULT_CAP = pathlib.Path(r"C:\Program Files (x86)\Steam\steamapps\common"
                           r"\AdventureQuest Worlds Unity Playtest\UserData\Beyond\packets.jsonl")


def mine_bank_items(cap_path):
    """item_id -> raw item def, unioned across every s2c loadBank in the capture."""
    items = {}
    with open(cap_path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if "oadBank" not in line:
                continue
            try:
                o = json.loads(line)
            except Exception:
                continue
            if o.get("dir") != "s2c":
                continue
            pkt = o.get("pkt") or {}
            if (pkt.get("Cmd") or "").lower() != "loadbank":
                continue
            for it in pkt.get("items") or []:
                if isinstance(it, dict) and it.get("ID") is not None:
                    items[int(it["ID"])] = it           # last write wins
    return items


def main():
    cap = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_CAP
    if not cap.exists():
        sys.exit(f"capture not found: {cap}")

    items = mine_bank_items(cap)
    if not items:
        sys.exit(f"no s2c loadBank items in {cap} — open your bank in-game while capturing.")

    db.init()
    with db.connect() as conn:
        before = conn.execute("SELECT COUNT(*) FROM items").fetchone()[0]
        for iid, it in items.items():
            conn.execute(
                "INSERT INTO items(item_id, name, item_type, raw) VALUES(?,?,?,?) "
                "ON CONFLICT(item_id) DO NOTHING",
                (iid, it.get("Name"), int(it.get("ItemType", 0) or 0),
                 json.dumps(db.item_template(it), separators=(",", ":"))))
        conn.commit()
        after = conn.execute("SELECT COUNT(*) FROM items").fetchone()[0]

    print(f"[import] capture: {cap}")
    print(f"[import] distinct bank item defs mined: {len(items)}")
    print(f"[import] catalog items: {before} -> {after}  (+{after - before} new)")


if __name__ == "__main__":
    main()
