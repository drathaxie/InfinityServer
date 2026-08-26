#!/usr/bin/env python3
r"""
Harvest real item definitions from LIVE AQW Infinity via `loadShop`, driven by an EXPLICIT list of
known-good shop IDs (not a blind range). The list comes from the community shop reference sheet,
filtered to Result == "Works*" — so we never load a "Disconnects" shop (those drop the socket) and
never burn the timeout on empty ids. loadShop can't be pipelined (server keeps one shop open at a
time), so this is serial — but only over ~2.3k real shops, ~20 min.

Resilient: if the socket drops mid-sweep anyway, it reconnects and continues. Resumable: re-run
skips item IDs already saved AND shop IDs already fully processed.

Creds via env (never logged): AE_USER, AE_PASS
Usage:
    python capture/harvest_shops_list.py --ids capture/harvest/works_shops.json --delay 0.35
Output (append-only, resumable):
    capture/harvest/shop_items_live.jsonl   one catalog item def per line (deduped by item ID)
    capture/harvest/shops_done.json         shop IDs fully processed
"""
import argparse, asyncio, json, os, pathlib, sys, urllib.parse, urllib.request

CK = "N7B5W8W1Y5B1R5VWVZ"
LU = "https://infinity.aq.com/game/api/login/nowinfinity"
VER = "0.0.252"
HOST, PORT = "sockett4.aq.com", 6150
HERE = pathlib.Path(__file__).resolve().parent
ITEMS_OUT = HERE / "harvest" / "shop_items_live.jsonl"
DONE_OUT = HERE / "harvest" / "shops_done.json"
ITEMS_OUT.parent.mkdir(parents=True, exist_ok=True)


def login():
    f = urllib.parse.urlencode({"user": os.environ["AE_USER"], "pass": os.environ["AE_PASS"],
                                "option": "2", "infinityVersion": VER}).encode()
    req = urllib.request.Request(LU, data=f, headers={"User-Agent": "Mozilla/5.0"})
    d = json.loads(urllib.request.urlopen(req, timeout=30).read())
    if not d.get("bSuccess"):
        raise SystemExit(f"login failed: {d.get('sMsg')!r}")
    a = d["account"]
    print(f"[login] ok as {a['unm']!r} (access {a.get('iAccess')})")
    return a["sToken"], a["unm"]


def load_json(p, default):
    try:
        return json.loads(pathlib.Path(p).read_text())
    except Exception:
        return default


def seen_item_ids():
    ids = set()
    if ITEMS_OUT.exists():
        for line in ITEMS_OUT.open(encoding="utf-8"):
            try:
                ids.add(int(json.loads(line)["ID"]))
            except Exception:
                pass
    return ids


async def connect(token, unm):
    reader, writer = await asyncio.open_connection(HOST, PORT)
    got = asyncio.Event()
    shop_fut = {"fut": None}

    async def read_loop():
        buf = bytearray()
        try:
            while True:
                chunk = await reader.read(65536)
                if not chunk:
                    return
                buf.extend(chunk)
                while b"\x00" in buf:
                    i = buf.index(0); raw = bytes(buf[:i]); del buf[:i + 1]
                    if not raw:
                        continue
                    try:
                        o = json.loads(raw.decode("utf-8", "replace"))
                    except Exception:
                        continue
                    c = (o.get("Cmd") or o.get("cmd") or "")
                    if c in ("loginResponse", "initPlayer"):
                        got.set()
                    elif c.lower() == "loadshop":
                        fut = shop_fut.get("fut")
                        if fut and not fut.done():
                            fut.set_result(o)
        except Exception:
            return

    rt = asyncio.create_task(read_loop())

    async def send(cmd, params=None):
        writer.write(json.dumps({"Cmd": cmd, "Params": params or []}).encode() + b"\x00")
        await writer.drain()

    await send("Login", [CK, unm, token])
    try:
        await asyncio.wait_for(got.wait(), timeout=15)
    except asyncio.TimeoutError:
        print("[socket] no login ack in 15s — proceeding")
    await send("firstJoin")
    await asyncio.sleep(1.0)
    return reader, writer, rt, shop_fut, send


async def run(ids, delay, timeout):
    token, unm = login()
    reader, writer, rt, shop_fut, send = await connect(token, unm)

    seen = seen_item_ids()
    done = set(load_json(DONE_OUT, []))
    todo = [s for s in ids if s not in done]
    print(f"[sweep] {len(todo)}/{len(ids)} shops to load ({len(seen)} items already saved)")

    n_shops = n_new = 0
    out = ITEMS_OUT.open("a", encoding="utf-8")
    for idx, sid in enumerate(todo, 1):
        fut = asyncio.get_event_loop().create_future()
        shop_fut["fut"] = fut
        try:
            await send("loadShop", [str(sid)])
            resp = await asyncio.wait_for(fut, timeout=timeout)
        except asyncio.TimeoutError:
            resp = None
        except (ConnectionError, OSError):
            resp = "DROP"
        if resp == "DROP":
            print(f"[socket] dropped at shop {sid} — reconnecting")
            try:
                writer.close()
            except Exception:
                pass
            rt.cancel()
            await asyncio.sleep(2.0)
            reader, writer, rt, shop_fut, send = await connect(token, unm)
            continue  # retry this shop next run (not marked done)
        items = ((resp or {}).get("shop") or {}).get("items") or []
        for it in items:
            iid = it.get("ID")
            if iid is None or int(iid) in seen:
                continue
            seen.add(int(iid))
            for k in ("ShopItemID", "QuantityRemain"):
                it.pop(k, None)
            out.write(json.dumps(it, separators=(",", ":")) + "\n")
            n_new += 1
        if items:
            n_shops += 1
        done.add(sid)
        if idx % 25 == 0 or items:
            out.flush()
            DONE_OUT.write_text(json.dumps(sorted(done)))
            print(f"  [{idx}/{len(todo)}] shop {sid}: {len(items)} items | {n_shops} shops, {n_new} new items")
        await asyncio.sleep(delay)

    out.flush(); out.close()
    DONE_OUT.write_text(json.dumps(sorted(done)))
    try:
        writer.close()
    except Exception:
        pass
    rt.cancel()
    print(f"[sweep] DONE: {n_shops} non-empty shops, {n_new} new items -> {ITEMS_OUT}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ids", required=True, help="JSON array of shop IDs to load")
    ap.add_argument("--delay", type=float, default=0.35)
    ap.add_argument("--timeout", type=float, default=6.0)
    args = ap.parse_args()
    if not os.environ.get("AE_USER") or not os.environ.get("AE_PASS"):
        sys.exit("set AE_USER and AE_PASS env vars before running.")
    ids = json.loads(pathlib.Path(args.ids).read_text())
    asyncio.run(run(ids, args.delay, args.timeout))


if __name__ == "__main__":
    main()
