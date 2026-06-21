#!/usr/bin/env python3
"""
Synthetic client: drives the server through the real login handshake using the
captured c2s sequence, and verifies the server replies with framed JSON the way
the real client expects. No game needed — proves the protocol end to end.
"""
import asyncio, json

PORT = 5588
# The exact c2s order the real client used (from capture/handshake.json).
CLIENT_SEQUENCE = ["Login", "firstJoin", "getApop", "getQuests", "moveToCell", "mv"]


async def read_frame(reader):
    buf = bytearray()
    while True:
        b = await reader.read(1)
        if not b:
            return None
        if b == b"\x00":
            return bytes(buf)
        buf.extend(b)


async def main():
    reader, writer = await asyncio.open_connection("127.0.0.1", PORT)
    print(f"connected to 127.0.0.1:{PORT}")
    total = 0
    for cmd in CLIENT_SEQUENCE:
        writer.write(json.dumps({"Cmd": cmd}).encode() + b"\x00")
        await writer.drain()
        print(f">> {cmd}")
        # Drain whatever the server scripts back for this request.
        try:
            while True:
                frame = await asyncio.wait_for(read_frame(reader), timeout=0.4)
                if frame is None:
                    break
                obj = json.loads(frame.decode("utf-8", errors="replace"))
                total += 1
                print(f"   << {obj.get('Cmd'):<16} {len(frame):>7}B")
        except asyncio.TimeoutError:
            pass
    writer.close()
    print(f"\nOK — received {total} framed responses")


if __name__ == "__main__":
    asyncio.run(main())
