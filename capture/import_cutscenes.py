#!/usr/bin/env python3
"""
Import captured Dialogger cutscenes (getDialog) straight into the LIVE DB.

A getDialog response carries only {Cmd, data:{JsonText}} — the cutscene id is in
the preceding c2s RequestGetDialog (Params=[id]). We pair each request with its
response and store non-empty JsonText into the cutscenes table (the same store
load_dialog serves and DialoggerSave writes). Non-destructive: ON CONFLICT DO
NOTHING, and empty payloads are skipped (AE itself returns "" for some ids).

NOTE: this covers Dialogger cutscenes (getDialog) only. The apop OpenCutscene
scenes use getCutscene (a CellData scene over TCP) — a separate system, not here.

Usage:
    python capture/import_cutscenes.py [path/to/packets.jsonl]
"""
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "server"))
import db          # noqa: E402

DEFAULT_CAP = pathlib.Path(r"C:\Program Files (x86)\Steam\steamapps\common"
                           r"\AdventureQuest Worlds Unity Playtest\UserData\Beyond\packets.jsonl")


def mine_cutscenes(cap_path):
    """id -> JsonText, pairing c2s getDialog (carries the id) with its s2c payload."""
    scenes, pending = {}, None
    with open(cap_path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if "getDialog" not in line:
                continue
            try:
                o = json.loads(line)
            except Exception:
                continue
            pkt = o.get("pkt") or {}
            if pkt.get("Cmd") != "getDialog":
                continue
            if o.get("dir") == "c2s":
                params = pkt.get("Params") or []
                pending = params[-1] if params else None
            elif o.get("dir") == "s2c" and pending is not None:
                data = pkt.get("data") or {}
                jt = data.get("JsonText") if isinstance(data, dict) else None
                if jt:                                  # skip empty payloads (AE returns "" for some)
                    scenes[int(pending)] = jt
                pending = None
    return scenes


def main():
    cap = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_CAP
    if not cap.exists():
        sys.exit(f"capture not found: {cap}")
    scenes = mine_cutscenes(cap)
    if not scenes:
        sys.exit(f"no non-empty getDialog cutscenes in {cap}")

    db.init()
    with db.connect() as conn:
        before = {r[0] for r in conn.execute("SELECT id FROM cutscenes").fetchall()}
        for cid, jt in scenes.items():
            conn.execute("INSERT INTO cutscenes(id, raw) VALUES(?,?) ON CONFLICT(id) DO NOTHING",
                         (cid, jt))
        conn.commit()
        after = {r[0] for r in conn.execute("SELECT id FROM cutscenes").fetchall()}

    added = sorted(after - before)
    print(f"[import] cutscenes mined: {sorted(scenes)} ({len(scenes)})")
    print(f"[import] newly added to live DB: {added} ({len(added)})")
    print(f"[import] cutscenes now in DB: {len(after)}")


if __name__ == "__main__":
    main()
