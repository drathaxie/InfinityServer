#!/usr/bin/env python3
"""
A headless second player, to prove multiplayer end-to-end without a 2nd client.

Logs in, joins a map, and paces back and forth sending `mv`. A real client in
the same map should see this bot appear (AreaAdd) and walk (movement). The bot
also prints presence/movement it receives, so you can see your own character
from the server's side too.

Usage:  python bot.py [mapName] [botName]
        python bot.py battleon bot_scout
"""
import asyncio, json, math, sys

HOST, PORT = "127.0.0.1", 5588
MAP = sys.argv[1] if len(sys.argv) > 1 else "battleon"
NAME = sys.argv[2] if len(sys.argv) > 2 else "bot_scout"


async def send(w, cmd, params=None):
    w.write(json.dumps({"Cmd": cmd, "Params": params or []}).encode() + b"\x00")
    await w.drain()


async def reader_task(r):
    buf = bytearray()
    while True:
        chunk = await r.read(65536)
        if not chunk:
            return
        buf.extend(chunk)
        while b"\x00" in buf:
            i = buf.index(0); raw = bytes(buf[:i]); del buf[:i+1]
            if not raw:
                continue
            try:
                o = json.loads(raw.decode("utf-8", "replace"))
            except Exception:
                continue
            c = o.get("Cmd")
            if c == "AreaAdd":
                print(f"  <- AreaAdd: {o.get('userData',{}).get('Name')} joined")
            elif c == "AreaRemove":
                print(f"  <- AreaRemove: {o.get('unm')} left")
            elif c == "movement":
                print(f"  <- movement: player {o.get('PlayerID')} -> "
                      f"({o['position']['x']:.1f},{o['position']['y']:.1f})")
            elif c == "chatm":
                print(f"  <- chat [{o.get('Name')}]: {o.get('msg')}")


async def main():
    r, w = await asyncio.open_connection(HOST, PORT)
    asyncio.create_task(reader_task(r))
    print(f"[bot {NAME}] login -> join {MAP}")
    await send(w, "Login", ["LOCAL", NAME, "x"]); await asyncio.sleep(0.4)
    await send(w, "firstJoin");                  await asyncio.sleep(0.3)
    await send(w, "tfer", [NAME, MAP, "1", "Enter", "Spawn"]); await asyncio.sleep(0.3)
    await send(w, "moveToCell", ["Enter", "Spawn", MAP]);      await asyncio.sleep(0.3)
    print(f"[bot {NAME}] pacing in {MAP}; Ctrl+C to stop")
    t = 0.0
    while True:
        x = 15.0 + 8.0 * math.sin(t)      # walk left/right
        y = -6.0
        dx = math.cos(t)
        await send(w, "mv", [f"{x:.3f}", f"{y:.3f}", f"{dx:.3f}", "0", f"{x:.3f}", f"{y:.3f}"])
        t += 0.5
        await asyncio.sleep(1.0)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n[bot] bye")
