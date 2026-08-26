import asyncio, json, os, urllib.parse, urllib.request
CK = "N7B5W8W1Y5B1R5VWVZ"
LU = "https://infinity.aq.com/game/api/login/nowinfinity"

def login():
    f = urllib.parse.urlencode({"user": os.environ["AE_USER"], "pass": os.environ["AE_PASS"],
                                "option": "2", "infinityVersion": "0.0.252"}).encode()
    req = urllib.request.Request(LU, data=f, headers={"User-Agent": "Mozilla/5.0"})
    d = json.loads(urllib.request.urlopen(req, timeout=30).read())
    a = d["account"]; return a["sToken"], a["unm"]

async def main():
    tok, unm = login()
    r, w = await asyncio.open_connection("sockett4.aq.com", 6150)
    async def send(c, p=None):
        w.write(json.dumps({"Cmd": c, "Params": p or []}).encode() + b"\x00"); await w.drain()
    await send("Login", [CK, unm, tok]); await asyncio.sleep(2.5)
    await send("firstJoin"); await asyncio.sleep(1.5)
    # burst: fire 300 shop loads back-to-back, no waiting
    for sid in range(1, 301):
        await send("loadShop", [str(sid)])
    seen = []; buf = bytearray()
    try:
        while len(seen) < 25:
            c = await asyncio.wait_for(r.read(65536), timeout=6)
            if not c:
                break
            buf.extend(c)
            while b"\x00" in buf:
                i = buf.index(0); raw = bytes(buf[:i]); del buf[:i + 1]
                if not raw:
                    continue
                try:
                    o = json.loads(raw.decode("utf-8", "replace"))
                except Exception:
                    continue
                if (o.get("Cmd") or "").lower() == "loadshop":
                    sh = o.get("shop") or {}
                    meta = {k: sh.get(k) for k in ("ShopID", "ID", "shopID", "sName", "Name", "iType")
                            if k in sh}
                    meta["item_count"] = len(sh.get("items") or [])
                    seen.append(meta)
    except asyncio.TimeoutError:
        pass
    print("shop responses received:", len(seen))
    for s in seen[:15]:
        print(json.dumps(s))

asyncio.run(main())
