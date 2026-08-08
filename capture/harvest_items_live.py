#!/usr/bin/env python3
r"""
Harvest real item definitions (Name + Description + Cost/Rarity/Level/... + Bundle) from the
LIVE AdventureQuest Worlds Infinity game socket via the `itemQuery` command — the only source
that carries flavor text (no REST endpoint exposes it, and shipped bundles have their
Description field stripped; verified).

This talks to Artix's LIVE production game server with an AUTHENTICATED account, and sweeps
item IDs one-by-one. That is scraping-shaped traffic against a third party's prod — it is
THROTTLED by default and resumable, and you should keep the account disposable. Use responsibly.

Login flow (reconstructed from the client decomp — UILoginActions.cs, RequestLogin.cs):
  1. HTTPS POST {WebApiURL}login/nowinfinity  form: user, pass, option=2, infinityVersion
     -> LoginData {account:{sToken, unm}, servers:[...]}
  2. TCP connect to the game socket (default sockett4.aq.com:6150 from data/Servers)
  3. send {"Cmd":"Login","Params":[CLIENT_KEY, unm, sToken]}   (null-terminated JSON)
  4. send {"Cmd":"firstJoin"}
  5. loop: send {"Cmd":"itemQuery","Params":[str(id)]} -> {"Cmd":"itemQuery","item":{...}}

Credentials come from env vars (never hardcode / never logged):
    AE_USER, AE_PASS

Usage (prove it first with a tiny range around a known item, then widen):
    AE_USER=... AE_PASS=... python capture/harvest_items_live.py --start 8010 --end 8015
    AE_USER=... AE_PASS=... python capture/harvest_items_live.py --start 1 --end 110000 --delay 0.5

Output (append-only JSONL, resumable — re-run skips ids already present):
    capture/harvest/items_live.jsonl     one {"id":N,"item":{...}} per line (item null if none)
"""
import argparse
import asyncio
import json
import os
import pathlib
import sys
import urllib.parse
import urllib.request

# The fixed client key the real client sends as Login Params[0] (UILoginActions.cs).
CLIENT_KEY = "N7B5W8W1Y5B1R5VWVZ"
LOGIN_URL = "https://infinity.aq.com/game/api/login/nowinfinity"
INFINITY_VERSION = "0.0.252"          # live infinityClientVersion (Data/InfinityVars)
DEFAULT_HOST, DEFAULT_PORT = "sockett4.aq.com", 6150

HERE = pathlib.Path(__file__).resolve().parent
OUT = HERE / "harvest" / "items_live.jsonl"
OUT.parent.mkdir(parents=True, exist_ok=True)


def web_login(user, password):
    """POST login/nowinfinity -> (token, unm, servers). Raises on failure."""
    form = urllib.parse.urlencode({
        "user": user, "pass": password, "option": "2", "infinityVersion": INFINITY_VERSION,
    }).encode()
    req = urllib.request.Request(LOGIN_URL, data=form, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        data = json.loads(r.read().decode("utf-8", "replace"))
    if not data.get("bSuccess"):
        raise SystemExit(f"login failed: {data.get('sMsg')!r}")
    acct = data.get("account") or {}
    token, unm = acct.get("sToken"), acct.get("unm")
    if not token or not unm:
        raise SystemExit("login ok but no sToken/unm in response")
    print(f"[login] ok as {unm!r} (access {acct.get('iAccess')}), token acquired")
    return token, unm, data.get("servers") or []


def already_done():
    """Set of ids already in the output file (for resume)."""
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


async def sweep(host, port, user, password, start, end, delay):
    token, unm, _servers = web_login(user, password)

    reader, writer = await asyncio.open_connection(host, port)
    print(f"[socket] connected {host}:{port}")

    # A shared place for the reader coroutine to hand itemQuery responses back to the loop.
    pending = {"id": None, "fut": None}
    got_login = asyncio.Event()

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
                cmd = o.get("Cmd") or o.get("cmd")
                if cmd in ("loginResponse", "initPlayer"):
                    got_login.set()
                elif cmd == "itemQuery":
                    fut = pending.get("fut")
                    if fut and not fut.done():
                        fut.set_result(o.get("item"))

    rt = asyncio.create_task(read_loop())

    await _send(writer, "Login", [CLIENT_KEY, unm, token])
    try:
        await asyncio.wait_for(got_login.wait(), timeout=15)
    except asyncio.TimeoutError:
        print("[socket] no loginResponse/initPlayer within 15s — proceeding anyway")
    await _send(writer, "firstJoin")
    await asyncio.sleep(1.0)

    done = already_done()
    print(f"[sweep] ids {start}..{end} delay {delay}s ({len(done)} already done, will skip)")
    n_found = n_null = 0
    with OUT.open("a", encoding="utf-8") as out:
        for iid in range(start, end + 1):
            if iid in done:
                continue
            fut = asyncio.get_event_loop().create_future()
            pending["id"], pending["fut"] = iid, fut
            await _send(writer, "itemQuery", [str(iid)])
            try:
                item = await asyncio.wait_for(fut, timeout=10)
            except asyncio.TimeoutError:
                item = None
            out.write(json.dumps({"id": iid, "item": item}, separators=(",", ":")) + "\n")
            out.flush()
            if item:
                n_found += 1
                if n_found <= 20 or n_found % 200 == 0:
                    print(f"  {iid:>6}  {item.get('Name','')!r}  "
                          f"desc={len((item.get('Description') or ''))}c")
            else:
                n_null += 1
            await asyncio.sleep(delay)

    writer.close()
    rt.cancel()
    print(f"[sweep] done: {n_found} items found, {n_null} empty -> {OUT}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--start", type=int, default=1)
    ap.add_argument("--end", type=int, default=110000)
    ap.add_argument("--delay", type=float, default=0.5, help="seconds between queries (be gentle)")
    ap.add_argument("--host", default=DEFAULT_HOST)
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = ap.parse_args()

    user, password = os.environ.get("AE_USER"), os.environ.get("AE_PASS")
    if not user or not password:
        sys.exit("set AE_USER and AE_PASS env vars (an AE account) before running.")

    asyncio.run(sweep(args.host, args.port, user, password, args.start, args.end, args.delay))


if __name__ == "__main__":
    main()
