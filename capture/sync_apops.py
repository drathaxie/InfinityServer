#!/usr/bin/env python3
"""
Sync the apop catalog from a live-AE capture into the DB — NEW and EDITED dialogue.

The mod's always-on packet logger records every getApop response while you play. AE keeps
adding NPCs and editing what they say; this folds the captured apops into the DB, UPSERTING
(unlike seed's insert-if-absent, so edits actually land) and reporting a clear diff. It is
NON-DESTRUCTIVE: it only touches apops present in the capture and never deletes — so it won't
clobber custom apops (e.g. our Tato) that AE doesn't have.

To refresh from AE: remove the infinity markers, play live AE, visit the NPCs whose dialogue
you want (the logger captures them), then run this against the LIVE DB.

Targeting the LIVE DB: this uses server/db.py, so set the PG env first — run it on the VM with
`.pg.env` sourced (after scp'ing your packets.jsonl up), or locally through the db tunnel. With
no PG env it talks to the local SQLite db instead (handy for a dry run).

Usage:
    python capture/sync_apops.py [packets.jsonl]            # DRY RUN: just show the diff
    python capture/sync_apops.py [packets.jsonl] --apply    # write the new + edited apops
"""
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "server"))
import db          # noqa: E402

DEFAULT_CAP = pathlib.Path(r"C:\Program Files (x86)\Steam\steamapps\common"
                           r"\AdventureQuest Worlds Unity Playtest\UserData\Beyond\packets.jsonl")


def mine_apops(cap_path):
    """apopID -> apop document, unioned across every s2c getApop in the capture (latest wins)."""
    apops = {}
    with open(cap_path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if '"getApop"' not in line:
                continue
            try:
                o = json.loads(line)
            except Exception:
                continue
            if o.get("dir") != "s2c":
                continue
            for aid, s in ((o.get("pkt") or {}).get("apopData") or {}).items():
                try:
                    apops[int(aid)] = json.loads(s) if isinstance(s, str) else s
                except Exception:
                    pass
    return apops


def main():
    args = sys.argv[1:]
    apply = "--apply" in args
    paths = [a for a in args if not a.startswith("--")]
    cap = pathlib.Path(paths[0]) if paths else DEFAULT_CAP
    if not cap.exists():
        print(f"capture not found: {cap}")
        return

    mined = mine_apops(cap)
    backend = "Postgres (LIVE)" if db.BACKEND == "postgres" else f"SQLite {db.DB_PATH}"
    print(f"capture: {cap}\n  {len(mined)} apop(s) in capture  |  target DB: {backend}\n")

    conn = db.connect()
    new, edited, unchanged = [], [], 0
    for aid, doc in sorted(mined.items()):
        name = doc.get("name") or f"Apop {aid}"
        row = conn.execute("SELECT raw FROM apops WHERE apop_id=?", (aid,)).fetchone()
        if row is None:
            new.append((aid, name))
            continue
        try:
            cur = json.loads(row["raw"])
        except Exception:
            cur = None
        if cur != doc:
            edited.append((aid, name))
        else:
            unchanged += 1

    print(f"  NEW dialogue:    {len(new)}")
    for aid, nm in new:
        print(f"    +  {aid:>5}  {nm}")
    print(f"  EDITED dialogue: {len(edited)}")
    for aid, nm in edited:
        print(f"    ~  {aid:>5}  {nm}")
    print(f"  unchanged:       {unchanged}")

    if not apply:
        print("\n(dry run — nothing written. Re-run with --apply to write the new + edited apops.)")
        return
    if not (new or edited):
        print("\nnothing to apply.")
        return
    for aid, _nm in new + edited:
        doc = mined[aid]
        doc["ID"] = aid
        nm = (doc.get("name") or f"Apop {aid}")
        conn.execute(
            "INSERT INTO apops(apop_id, name, raw) VALUES(?,?,?) "
            "ON CONFLICT(apop_id) DO UPDATE SET name=excluded.name, raw=excluded.raw",
            (aid, nm, json.dumps(doc, separators=(",", ":"))))
    conn.commit()
    print(f"\napplied: {len(new)} new + {len(edited)} edited apop(s) written to {backend}.")


if __name__ == "__main__":
    main()
