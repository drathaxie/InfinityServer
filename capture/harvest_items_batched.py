#!/usr/bin/env python3
r"""
Harvest real item definitions from the LIVE AQW Infinity game socket via `itemQuery`, PIPELINED:
fire a whole window of ids at once (the reply carries the item's own `ID`, so out-of-order
responses are matched back by id) instead of one-at-a-time with a per-item wait. A window of ~100
mirrors what a single `loadShop` naturally returns, so this pulls the FULL id space — including
never-sold / unreleased content that shop-sweeping (harvest_shops_live.py) structurally can't reach.

itemQuery vs loadShop: a sibling harvester claims the live server ignores itemQuery. That is NOT
settled — so PROVE IT FIRST on a small range of known-real ids (default 1..200) and confirm defs
come back before widening. If itemQuery really is dead against AE, this prints all-null and we fall
back to loadShop for the shop-covered subset.

Batching model (handles nulls cleanly): send every id in a window, collect replies for `--window-timeout`
seconds, attribute each returned item by its ID; any id in the window that never answered is recorded
null. Resumable: re-run skips ids already in the output. Throttled between windows; keep the account
disposable (bulk queries against a third party's prod).

Credentials via env (never hardcoded / logged): AE_USER, AE_PASS

Usage:
    # PROVE it first (small, known-real ids):
    AE_USER=... AE_PASS=... python capture/harvest_items_batched.py --start 1 --end 200 --window 100
    # then widen to the full mirror:
    AE_USER=... AE_PASS=... python capture/harvest_items_batched.py --start 1 --end 110000 --window 100 --delay 0.4

Output (append-only JSONL, resumable):
    capture/harvest/items_live.jsonl     one {"id":N,"item":{...}|null} per line
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
OUT = HERE / "harvest" / "items_live.jsonl"
OUT.parent.mkdir(parents=True, exist_ok=True)


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


def already_done():
    done = set()
    if OUT.exists():
        with OUT.open(encoding="utf-8") as f:
            for line in f:
                try:
                    done.add(int(json.loads(line)["id"]))
                except Exception:
                    pass
    return done


async def _send(w, cmd, params=None):
    w.write(json.dumps({"Cmd": cmd, "Params": params or []}).encode() + b"\x00")
    await w.drain()


async def sweep(host, port, user, password, start, end, window, window_timeout, delay):
    token, unm = web_login(user, password)
    reader, writer = await asyncio.open_connection(host, port)
    print(f"[socket] connected {host}:{port}")

    got_login = asyncio.Event()
    # id -> item dict, filled by the reader as itemQuery replies stream in.
    inbox = {}

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
                elif cmd == "itemQuery":
                    item = o.get("item")
                    if isinstance(item, dict) and item.get("ID") is not None:
                        try:
                            inbox[int(item["ID"])] = item
                        except (ValueError, TypeError):
                            pass

    rt = asyncio.create_task(read_loop())
    await _send(writer, "Login", [CLIENT_KEY, unm, token])
    try:
        await asyncio.wait_for(got_login.wait(), timeout=15)
    except asyncio.TimeoutError:
        print("[socket] no login ack in 15s — proceeding")
    await _send(writer, "firstJoin")
    await asyncio.sleep(1.0)

    done = already_done()
    todo = [i for i in range(start, end + 1) if i not in done]
    print(f"[sweep] {len(todo)} ids in {start}..{end} (window {window}, {len(done)} already done)")

    n_found = n_null = 0
    with OUT.open("a", encoding="utf-8") as out:
        for base in range(0, len(todo), window):
            win = todo[base:base + window]
            for iid in win:
                inbox.pop(iid, None)
                await _send(writer, "itemQuery", [str(iid)])
            # let replies stream in; stop early once every id in the window has answered
            waited = 0.0
            while waited < window_timeout and not all(i in inbox for i in win):
                await asyncio.sleep(0.1)
                waited += 0.1
            for iid in win:
                item = inbox.pop(iid, None)
                out.write(json.dumps({"id": iid, "item": item}, separators=(",", ":")) + "\n")
                if item:
                    n_found += 1
                else:
                    n_null += 1
            out.flush()
            print(f"  ..{win[-1]:>6}  found={n_found} null={n_null}")
            await asyncio.sleep(delay)

    writer.close()
    rt.cancel()
    print(f"[sweep] done: {n_found} items, {n_null} empty -> {OUT}")
    if n_found == 0:
        print("[!] ZERO items returned — itemQuery may be ignored by the live server; "
              "use harvest_shops_live.py (loadShop) instead.")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--start", type=int, default=1)
    ap.add_argument("--end", type=int, default=200)
    ap.add_argument("--window", type=int, default=100, help="ids fired per batch before waiting")
    ap.add_argument("--window-timeout", type=float, default=8.0, help="max secs to wait for a window's replies")
    ap.add_argument("--delay", type=float, default=0.4, help="secs between windows (be gentle)")
    ap.add_argument("--host", default=DEFAULT_HOST)
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = ap.parse_args()
    user, password = os.environ.get("AE_USER"), os.environ.get("AE_PASS")
    if not user or not password:
        sys.exit("set AE_USER and AE_PASS env vars before running.")
    asyncio.run(sweep(args.host, args.port, user, password,
                      args.start, args.end, args.window, args.window_timeout, args.delay))


if __name__ == "__main__":
    main()
