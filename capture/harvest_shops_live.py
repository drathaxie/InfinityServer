#!/usr/bin/env python3
r"""
Harvest real item definitions (Name + Description + Cost/Rarity/Level/Bundle/...) from the LIVE
AQW Infinity game socket by sweeping the `loadShop` command over the shop-ID space.

Why loadShop and not itemQuery: `itemQuery` is defined in the client but NEVER invoked (the
server ignores it — verified). `loadShop` IS the real path — opening a shop returns every item
in it as a FULL definition WITH Description, ~4-20 items per request. So sweeping shop IDs pulls
real flavor text far more efficiently than one item at a time, using a command the client
actually uses.

Coverage caveat: this only yields items that appear in SOME shop. Items never sold anywhere
(much unreleased/WIP content) won't surface here — that's a genuine ceiling, not a bug.

Authenticated against Artix's LIVE prod; THROTTLED + resumable. Keep the account disposable.

Login flow (UILoginActions.cs): POST login/nowinfinity -> sToken/unm; socket Login
[CLIENT_KEY, unm, sToken]; firstJoin; then loadShop per id.

Credentials via env (never hardcoded/logged): AE_USER, AE_PASS

Usage:
    AE_USER=... AE_PASS=... python capture/harvest_shops_live.py --start 1 --end 2000 --delay 0.3

Output (append-only JSONL, resumable):
    capture/harvest/shop_items_live.jsonl   one item def per line (deduped by item ID on load)
    capture/harvest/shops_seen.json         {shop_id: item_count} for what returned
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
INFINITY_VERSION = "0.0.244"
DEFAULT_HOST, DEFAULT_PORT = "sockett4.aq.com", 6150

HERE = pathlib.Path(__file__).resolve().parent
ITEMS_OUT = HERE / "harvest" / "shop_items_live.jsonl"
SHOPS_OUT = HERE / "harvest" / "shops_seen.json"
ITEMS_OUT.parent.mkdir(parents=True, exist_ok=True)


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


def seen_items():
    ids = set()
    if ITEMS_OUT.exists():
        with ITEMS_OUT.open(encoding="utf-8") as f:
            for line in f:
                try:
                    ids.add(int(json.loads(line)["ID"]))
                except Exception:
                    pass
    return ids


async def _send(w, cmd, params=None):
    w.write(json.dumps({"Cmd": cmd, "Params": params or []}).encode() + b"\x00")
    await w.drain()


async def sweep(host, port, user, password, start, end, delay):
    token, unm = web_login(user, password)
    reader, writer = await asyncio.open_connection(host, port)
    print(f"[socket] connected {host}:{port}")

    got_login = asyncio.Event()
    shop_fut = {"fut": None}

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
                elif cmd.lower() == "loadshop":
                    fut = shop_fut.get("fut")
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

    seen = seen_items()
    shops = json.loads(SHOPS_OUT.read_text()) if SHOPS_OUT.exists() else {}
    print(f"[sweep] shops {start}..{end} delay {delay}s ({len(seen)} items already saved)")

    n_shops = n_new = 0
    with ITEMS_OUT.open("a", encoding="utf-8") as out:
        for sid in range(start, end + 1):
            fut = asyncio.get_event_loop().create_future()
            shop_fut["fut"] = fut
            await _send(writer, "loadShop", [str(sid)])
            try:
                resp = await asyncio.wait_for(fut, timeout=8)
            except asyncio.TimeoutError:
                resp = None
            items = ((resp or {}).get("shop") or {}).get("items") or []
            if items:
                n_shops += 1
                shops[str(sid)] = len(items)
                for it in items:
                    iid = it.get("ID")
                    if iid is None or int(iid) in seen:
                        continue
                    seen.add(int(iid))
                    # strip the per-shop instance fields; keep the catalog def
                    for k in ("ShopItemID", "QuantityRemain"):
                        it.pop(k, None)
                    out.write(json.dumps(it, separators=(",", ":")) + "\n")
                    n_new += 1
                out.flush()
                print(f"  shop {sid:>5}: {len(items):>3} items ({n_new} new items total)")
            await asyncio.sleep(delay)

    SHOPS_OUT.write_text(json.dumps(shops, indent=1), encoding="utf-8")
    writer.close()
    rt.cancel()
    print(f"[sweep] done: {n_shops} non-empty shops, {n_new} new items -> {ITEMS_OUT}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--start", type=int, default=1)
    ap.add_argument("--end", type=int, default=2000)
    ap.add_argument("--delay", type=float, default=0.3)
    ap.add_argument("--host", default=DEFAULT_HOST)
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = ap.parse_args()
    user, password = os.environ.get("AE_USER"), os.environ.get("AE_PASS")
    if not user or not password:
        sys.exit("set AE_USER and AE_PASS env vars before running.")
    asyncio.run(sweep(args.host, args.port, user, password, args.start, args.end, args.delay))


if __name__ == "__main__":
    main()
