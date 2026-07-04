#!/usr/bin/env python3
"""
Import re-captured shops straight into the LIVE DB.

Unlike the data/*.json extractors, this writes nothing to disk-as-a-bootstrap:
it reads every s2c `loadShop` packet out of a capture and normalizes each one
into the live `infinity.db` via the server's own write path (`seed._seed_shop` —
shop meta + the shared `items` catalog + `shop_items` links). The running server
then serves them live, exactly like shop 2468 and the live-edited apops.

Usage:
    python capture/import_shops.py [path/to/packets.jsonl]

The capture path defaults to the live Steam playtest log (same as the other
extract_*.py scripts) and can be overridden as the first argument so you can
point it at a fresh capture anywhere.
"""
import glob
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "server"))
import db          # noqa: E402  (live DB connection)
import seed        # noqa: E402  (_seed_shop: the canonical normalize-into-DB path)

DEFAULT_CAP = pathlib.Path(r"C:\Program Files (x86)\Steam\steamapps\common"
                           r"\AdventureQuest Worlds Unity Playtest\UserData\Beyond\packets.jsonl")
APOPS_DIR = pathlib.Path(__file__).resolve().parent.parent / "data" / "apops"


def referenced_shop_ids():
    """Every shopID the game can open: `intMin` on each apop `ItemShop` button."""
    refs = set()

    def walk(o):
        if isinstance(o, dict):
            if o.get("action") == "ItemShop" and o.get("intMin"):
                refs.add(int(o["intMin"]))
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    for p in glob.glob(str(APOPS_DIR / "*.json")):
        try:
            walk(json.loads(pathlib.Path(p).read_text(encoding="utf-8")))
        except Exception:
            continue
    return refs


def mine_shops(cap_path):
    """shopID -> loadShop packet, deduped across the whole capture (last wins)."""
    shops = {}
    with open(cap_path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if '"loadShop"' not in line:
                continue
            try:
                o = json.loads(line)
            except Exception:
                continue
            if o.get("dir") != "s2c":
                continue
            pkt = o.get("pkt") or {}
            if pkt.get("Cmd") != "loadShop":
                continue
            shop = pkt.get("shop")
            if isinstance(shop, dict) and shop.get("shopID") is not None:
                shops[int(shop["shopID"])] = pkt
    return shops


def main():
    cap = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_CAP
    if not cap.exists():
        sys.exit(f"capture not found: {cap}\n"
                 f"capture some shops first, or pass the path: "
                 f"python capture/import_shops.py <packets.jsonl>")

    shops = mine_shops(cap)
    if not shops:
        sys.exit(f"no s2c loadShop packets in {cap} — open the shops in-game while capturing.")

    db.init()
    items = 0
    with db.connect() as conn:
        before = {r[0] for r in conn.execute("SELECT shop_id FROM shops").fetchall()}
        for pkt in shops.values():
            items += seed._seed_shop(conn, pkt)
        conn.commit()
        after = {r[0] for r in conn.execute("SELECT shop_id FROM shops").fetchall()}

    added = sorted(after - before)
    print(f"[import] capture: {cap}")
    print(f"[import] loadShop packets mined: {len(shops)} ({sorted(shops)})")
    print(f"[import] shops newly added to live DB: {len(added)} ({added})")
    print(f"[import] shop_item links written: {items}")

    # Tell the owner exactly what is still un-captured, so re-capture is targeted.
    referenced = referenced_shop_ids()
    missing = sorted(referenced - after)
    print(f"[import] shops referenced by apops: {len(referenced)}; "
          f"now in live DB: {len(referenced & after)}; still missing: {len(missing)}")
    if missing:
        print(f"[import] STILL MISSING (capture these next): {missing}")


if __name__ == "__main__":
    main()
