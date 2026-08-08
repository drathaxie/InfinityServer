#!/usr/bin/env python3
r"""
Harvest real Dialogger cutscenes (getDialog) from the LIVE AQW Infinity socket by requesting
dialog IDs directly — the faithful way to fill gaps in data/cutscenes.json (e.g. the lair
scene 73 for quest 59 "Find DragonSlayer Armor", which our capture only ever saw as the
fade-and-complete fallback).

getDialog is SOCKET-ONLY (no REST equivalent — RequestGetDialog just sends {Cmd:"getDialog",
Params:[id]}), so unlike the bundle catalog we must log in and ask over the socket. Same proven
login + socket flow as harvest_shops_live.py. A getDialog response carries {data:{JsonText}};
AE returns an empty JsonText for ids with no scene, which we skip.

By default it merges every non-empty scene into data/cutscenes.json WITHOUT clobbering ids we
already have (mirrors the seed's ON CONFLICT DO NOTHING), so re-running is safe and only fills
holes. Deploy: scp data/cutscenes.json to the VM and restart infinity-game (seed inserts it).

Authenticated against Artix's LIVE prod; keep the account disposable. Credentials via env
(never hardcoded/logged): AE_USER, AE_PASS.

Usage:
    AE_USER=... AE_PASS=... python capture/harvest_cutscenes_live.py --ids 73
    AE_USER=... AE_PASS=... python capture/harvest_cutscenes_live.py --start 70 --end 90
    # add --no-merge to only write the jsonl record and leave data/cutscenes.json untouched
"""
import argparse
import asyncio
import json
import os
import pathlib
import sys
import urllib.parse
import urllib.request

CLIENT_KEY = "N7B5W8W1Y5B1R5VWVZ"
LOGIN_URL = "https://infinity.aq.com/game/api/login/nowinfinity"
INFINITY_VERSION = "0.0.252"
DEFAULT_HOST, DEFAULT_PORT = "sockett4.aq.com", 6150

HERE = pathlib.Path(__file__).resolve().parent
DIALOGS_OUT = HERE / "harvest" / "dialogs_live.jsonl"
CUTSCENES_JSON = HERE.parent / "data" / "cutscenes.json"
DIALOGS_OUT.parent.mkdir(parents=True, exist_ok=True)


def web_login(user, password):
    form = urllib.parse.urlencode({
        "user": user, "pass": password, "option": "2", "infinityVersion": INFINITY_VERSION,
    }).encode()
    req = urllib.request.Request(LOGIN_URL, data=form, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        data = json.loads(r.read().decode("utf-8", "replace"))
    if not data.get("bSuccess"):
        raise SystemExit(f"login failed: {data.get('sMsg')!r}")
    acct = data.get("account") or {}
    if not acct.get("sToken") or not acct.get("unm"):
        raise SystemExit("login ok but no sToken/unm")
    print(f"[login] ok as {acct['unm']!r} (access {acct.get('iAccess')})")
    return acct["sToken"], acct["unm"]


async def _send(w, cmd, params=None):
    w.write(json.dumps({"Cmd": cmd, "Params": params or []}).encode() + b"\x00")
    await w.drain()


async def harvest(host, port, user, password, ids, delay, merge):
    token, unm = web_login(user, password)
    reader, writer = await asyncio.open_connection(host, port)
    print(f"[socket] connected {host}:{port}")

    got_login = asyncio.Event()
    dlg_fut = {"fut": None}

    async def read_loop():
        buf = bytearray()
        while True:
            chunk = await reader.read(65536)
            if not chunk:
                return
            buf.extend(chunk)
            while b"\x00" in buf:
                i = buf.index(0)
                raw = bytes(buf[:i])
                del buf[: i + 1]
                if not raw:
                    continue
                try:
                    o = json.loads(raw.decode("utf-8", "replace"))
                except Exception:
                    continue
                cmd = (o.get("Cmd") or o.get("cmd") or "")
                if cmd in ("loginResponse", "initPlayer"):
                    got_login.set()
                elif cmd == "getDialog":
                    fut = dlg_fut.get("fut")
                    if fut and not fut.done():
                        fut.set_result(o)

    rt = asyncio.create_task(read_loop())
    await _send(writer, "Login", [CLIENT_KEY, unm, token])
    try:
        await asyncio.wait_for(got_login.wait(), timeout=15)
    except asyncio.TimeoutError:
        print("[socket] no login ack in 15s — proceeding")
    await _send(writer, "firstJoin")
    await asyncio.sleep(1.0)

    scenes = {}                                     # id -> JsonText (non-empty only)
    with DIALOGS_OUT.open("a", encoding="utf-8") as out:
        for did in ids:
            fut = asyncio.get_event_loop().create_future()
            dlg_fut["fut"] = fut
            await _send(writer, "getDialog", [str(did)])
            try:
                # Holes on AE's socket often produce no response at all. Valid scenes answer
                # immediately; a short timeout keeps comprehensive sparse-ID sweeps practical.
                resp = await asyncio.wait_for(fut, timeout=1.0)
            except asyncio.TimeoutError:
                resp = None
            data = ((resp or {}).get("pkt") or resp or {}).get("data") or {}
            jt = data.get("JsonText") if isinstance(data, dict) else None
            # AE returns "" or an empty/stub object for holes. Some REAL early cutscenes are
            # unnamed (71 is one), so name is not a valid presence test; require substantive
            # frame commands instead.
            try:
                parsed = json.loads(jt) if jt else {}
                frames = parsed.get("frames") if isinstance(parsed, dict) else None
                command_count = sum(len(f) for f in frames if isinstance(f, list)) if frames else 0
                has_scene = command_count > 1
            except Exception:
                has_scene = False
            if has_scene:
                scenes[str(did)] = jt
                out.write(json.dumps({"id": did, "JsonText": jt}, separators=(",", ":")) + "\n")
                out.flush()
                try:
                    nm = json.loads(jt).get("cutsceneName") or "(unnamed)"
                except Exception:
                    nm = "(unparseable)"
                print(f"  dialog {did:>4}: {len(jt):>6} bytes  {nm!r}")
            else:
                print(f"  dialog {did:>4}: empty / no scene")
            await asyncio.sleep(delay)

    writer.close()
    rt.cancel()
    print(f"[harvest] {len(scenes)} non-empty scenes -> {DIALOGS_OUT}")

    if merge and scenes:
        existing = {}
        if CUTSCENES_JSON.exists():
            existing = json.loads(CUTSCENES_JSON.read_text(encoding="utf-8"))
        added = [cid for cid in scenes if cid not in existing]   # ON CONFLICT DO NOTHING
        for cid in added:
            existing[cid] = scenes[cid]
        CUTSCENES_JSON.write_text(json.dumps(existing, indent=1, ensure_ascii=False),
                                  encoding="utf-8")
        print(f"[merge] added {sorted(added, key=int)} to {CUTSCENES_JSON} "
              f"({len(existing)} total; existing ids left untouched)")
        if added:
            print("[next] deploy: scp data/cutscenes.json to the VM, then restart infinity-game "
                  "(seed inserts the new ids; ON CONFLICT DO NOTHING keeps live edits).")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ids", type=str, help="comma-separated dialog ids (e.g. 73,80,81)")
    ap.add_argument("--start", type=int, help="range start (inclusive)")
    ap.add_argument("--end", type=int, help="range end (inclusive)")
    ap.add_argument("--delay", type=float, default=0.3)
    ap.add_argument("--host", default=DEFAULT_HOST)
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--no-merge", action="store_true",
                    help="only write the jsonl record; don't touch data/cutscenes.json")
    args = ap.parse_args()

    if args.ids:
        ids = [int(x) for x in args.ids.split(",") if x.strip()]
    elif args.start is not None and args.end is not None:
        ids = list(range(args.start, args.end + 1))
    else:
        ids = [73]                                  # the known-missing lair scene (quest 59)

    user, password = os.environ.get("AE_USER"), os.environ.get("AE_PASS")
    if not user or not password:
        sys.exit("set AE_USER and AE_PASS env vars before running.")
    asyncio.run(harvest(args.host, args.port, user, password, ids, args.delay, not args.no_merge))


if __name__ == "__main__":
    main()
