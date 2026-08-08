#!/usr/bin/env python3
"""Join missing live AE map bundles and save real AreaJoin definitions.

Only successful AreaJoin responses are written. MAP-typed cinematic/background
assets that are not joinable remain asset records and never become fake maps.
"""
import argparse
import asyncio
import json
import os
import pathlib
import re
import urllib.parse
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
BUNDLES = ROOT / "capture" / "harvest" / "bundles_catalog.json"
MAPS = ROOT / "data" / "maps"
LOGIN_URL = "https://infinity.aq.com/game/api/login/nowinfinity"
CLIENT_KEY = "N7B5W8W1Y5B1R5VWVZ"
VERSION = "0.0.252"


def login(user, password):
    body = urllib.parse.urlencode({"user": user, "pass": password, "option": "2",
                                  "infinityVersion": VERSION}).encode()
    req = urllib.request.Request(LOGIN_URL, data=body, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as response:
        data = json.loads(response.read().decode("utf-8", "replace"))
    if not data.get("bSuccess"):
        raise SystemExit(f"login failed: {data.get('sMsg')!r}")
    account = data.get("account") or {}
    return account["sToken"], account["unm"]


def safe(name):
    return "".join(c if c.isalnum() else "_" for c in name).lower()


def candidates(bundle):
    filename = bundle.get("FileName") or ""
    stem = pathlib.PurePosixPath(filename).stem
    stem = re.sub(r"^\d+_", "", stem, flags=re.I)
    name = str(bundle.get("Name") or "")
    parent = pathlib.PurePosixPath(filename).parent.name
    raw = [name, stem, parent]
    out = []
    for value in raw:
        value = re.sub(r"^(map|house)-", "", value, flags=re.I)
        for variant in (value, value.replace("-", ""), value.replace("_", "")):
            variant = variant.strip().lower()
            if variant and variant not in out:
                out.append(variant)
    return out


def missing_bundles():
    rows = json.loads(BUNDLES.read_text(encoding="utf-8"))
    have = set()
    for path in MAPS.glob("*.json"):
        try:
            area = (json.loads(path.read_text(encoding="utf-8")) or {}).get("area") or {}
            have.add(int((area.get("Bundle") or {}).get("ID") or 0))
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            continue
    return [row for row in rows if row.get("Type") == "MAP"
            and int(row.get("VersionLive") or 0) > 0 and int(row["ID"]) not in have]


async def send(writer, cmd, params=None):
    writer.write(json.dumps({"Cmd": cmd, "Params": params or []}).encode() + b"\0")
    await writer.drain()


async def run(args):
    token, unm = login(os.environ["AE_USER"], os.environ["AE_PASS"])
    reader, writer = await asyncio.open_connection(args.host, args.port)
    packets = asyncio.Queue()

    async def read_loop():
        buf = bytearray()
        while chunk := await reader.read(65536):
            buf.extend(chunk)
            while b"\0" in buf:
                pos = buf.index(0)
                raw = bytes(buf[:pos]); del buf[:pos + 1]
                try:
                    obj = json.loads(raw.decode("utf-8", "replace"))
                except (ValueError, UnicodeDecodeError):
                    continue
                await packets.put(obj)

    task = asyncio.create_task(read_loop())
    await send(writer, "Login", [CLIENT_KEY, unm, token])
    await asyncio.sleep(1)
    await send(writer, "firstJoin")
    await asyncio.sleep(2)
    while not packets.empty():
        packets.get_nowait()

    found = {}
    rows = missing_bundles()
    print(f"missing live MAP bundles={len(rows)}")
    for bundle in rows:
        area = None
        tried = []
        for mapname in candidates(bundle):
            tried.append(mapname)
            await send(writer, "tfer", [unm, mapname, "0", "Enter", "Spawn"])
            deadline = asyncio.get_running_loop().time() + args.timeout
            while asyncio.get_running_loop().time() < deadline:
                try:
                    obj = await asyncio.wait_for(
                        packets.get(), deadline - asyncio.get_running_loop().time())
                except asyncio.TimeoutError:
                    break
                if (obj.get("Cmd") or "").lower() == "areajoin":
                    area = obj
                    break
            if area:
                break
        if not area:
            print(f"  asset {bundle['ID']}: not joinable ({', '.join(tried)})")
            continue
        actual = area.get("strMapName") or area.get("areaName") or tried[-1]
        base = str(actual).split("-")[0]
        found[int(bundle["ID"])] = base
        if args.apply:
            path = MAPS / f"{safe(base)}.json"
            path.write_text(json.dumps({"area": area, "cells": {}}, separators=(",", ":")),
                            encoding="utf-8")
        print(f"  asset {bundle['ID']}: {tried[-1]} -> {actual}")

    writer.close()
    task.cancel()
    print(f"successful real maps={len(found)} apply={args.apply}")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--host", default="sockett4.aq.com")
    ap.add_argument("--port", type=int, default=6150)
    ap.add_argument("--timeout", type=float, default=1.5)
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    if not os.environ.get("AE_USER") or not os.environ.get("AE_PASS"):
        raise SystemExit("set AE_USER and AE_PASS")
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
