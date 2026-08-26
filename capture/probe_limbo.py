import asyncio, json, os, urllib.parse, urllib.request
CK = "N7B5W8W1Y5B1R5VWVZ"; LU = "https://infinity.aq.com/game/api/login/nowinfinity"
def login():
    f = urllib.parse.urlencode({"user": os.environ["AE_USER"], "pass": os.environ["AE_PASS"],
                                "option": "2", "infinityVersion": "0.0.252"}).encode()
    d = json.loads(urllib.request.urlopen(urllib.request.Request(LU, data=f, headers={"User-Agent": "Mozilla/5.0"}), timeout=30).read())
    a = d["account"]; return a["sToken"], a["unm"]

async def main():
    tok, unm = login()
    r, w = await asyncio.open_connection("sockett4.aq.com", 6150)
    async def send(c, p=None):
        w.write(json.dumps({"Cmd": c, "Params": p or []}).encode() + b"\x00"); await w.drain()
    results = {}
    async def send_and_collect(label, cmd, params, secs):
        await send(cmd, params)
        buf = bytearray(); end = asyncio.get_event_loop().time() + secs
        while asyncio.get_event_loop().time() < end:
            try:
                c = await asyncio.wait_for(r.read(65536), timeout=secs)
            except asyncio.TimeoutError:
                break
            if not c: break
            buf.extend(c)
            while b"\x00" in buf:
                i = buf.index(0); raw = bytes(buf[:i]); del buf[:i+1]
                if not raw: continue
                try: o = json.loads(raw.decode("utf-8","replace"))
                except: continue
                cm = (o.get("Cmd") or o.get("cmd") or "")
                if cm.lower() == "loadshop":
                    sh = o.get("shop") or {}
                    results[label] = (sh.get("shopID"), sh.get("Name"), len(sh.get("items") or []))
                if cm in ("moveToArea","OOB","joinResult") or "rror" in json.dumps(o)[:200].lower():
                    results.setdefault(label+"_note", json.dumps(o)[:160])
    await send("Login", [CK, unm, tok]); await asyncio.sleep(2.5)
    await send("firstJoin"); await asyncio.sleep(1.5)
    # join limbo
    await send_and_collect("join_limbo", "tfer", [unm, "limbo", "1", "Enter", "Spawn"], 5)
    # now try shops that were empty from spawn
    for sid in (3, 26, 100, 1001):
        await send_and_collect(f"shop_{sid}", "loadShop", [str(sid)], 5)
    print(json.dumps(results, indent=1))

asyncio.run(main())
