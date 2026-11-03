#!/usr/bin/env python3
r"""Harvest quests and apops from AE's authenticated Infinity game socket.

These catalogs have no public REST endpoint. The client requests them with:

    {"Cmd":"getQuests", "Params":["287","288",...]}
    {"Cmd":"getApop",   "Params":["248,250,..."]}

The default mode is report-only: dated snapshots are written under
``capture/harvest`` and ``data/quests.json`` / ``data/apops.json`` are not
changed. Credentials come from ``AE_USER`` and ``AE_PASS``.
"""
import argparse
import asyncio
import datetime as dt
import json
import os
import pathlib
import sys
import urllib.parse
import urllib.request


CLIENT_KEY = "N7B5W8W1Y5B1R5VWVZ"
LOGIN_URL = "https://infinity.aq.com/game/api/login/nowinfinity"
INFINITY_VERSION = "0.0.261"
DEFAULT_HOST, DEFAULT_PORT = "sockett4.aq.com", 6150

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / "capture" / "harvest"
OUT.mkdir(parents=True, exist_ok=True)


def web_login(user, password):
    form = urllib.parse.urlencode({
        "user": user,
        "pass": password,
        "option": "2",
        "infinityVersion": INFINITY_VERSION,
    }).encode()
    req = urllib.request.Request(LOGIN_URL, data=form, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as response:
        data = json.loads(response.read().decode("utf-8", "replace"))
    if not data.get("bSuccess"):
        raise SystemExit(f"login failed: {data.get('sMsg')!r}")
    account = data.get("account") or {}
    if not account.get("sToken") or not account.get("unm"):
        raise SystemExit("login succeeded but returned no token/username")
    print(f"[login] ok as {account['unm']!r} (access {account.get('iAccess')})")
    return account["sToken"], account["unm"]


async def send(writer, cmd, params=None):
    writer.write(json.dumps({"Cmd": cmd, "Params": params or []}).encode() + b"\x00")
    await writer.drain()


def chunks(ids, size):
    for offset in range(0, len(ids), size):
        yield ids[offset:offset + size]


def load_dict(path):
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return {int(key): value for key, value in data.items()}


def compare(label, live, source):
    added = sorted(live.keys() - source.keys())
    changed = sorted(key for key in live.keys() & source.keys() if live[key] != source[key])
    source_only = sorted(source.keys() - live.keys())
    print(
        f"[{label}] live={len(live)} source={len(source)} "
        f"added={len(added)} changed={len(changed)} source_only={len(source_only)}"
    )
    if added:
        print(f"[{label}] added: " + ",".join(map(str, added)))
    if changed:
        print(f"[{label}] changed: " + ",".join(map(str, changed)))
    return {"added": added, "changed": changed, "source_only": source_only}


async def harvest(args, user, password):
    source_quests = load_dict(ROOT / "data" / "quests.json")
    source_apops = load_dict(ROOT / "data" / "apops.json")
    token, username = web_login(user, password)
    reader, writer = await asyncio.open_connection(args.host, args.port)
    print(f"[socket] connected {args.host}:{args.port}")

    login_event = asyncio.Event()
    queues = {"getquests": asyncio.Queue(), "getapop": asyncio.Queue()}

    async def read_loop():
        buf = bytearray()
        while True:
            chunk = await reader.read(65536)
            if not chunk:
                return
            buf.extend(chunk)
            while b"\x00" in buf:
                index = buf.index(0)
                raw = bytes(buf[:index])
                del buf[:index + 1]
                if not raw:
                    continue
                try:
                    payload = json.loads(raw.decode("utf-8", "replace"))
                except Exception:
                    continue
                cmd = str(payload.get("Cmd") or payload.get("cmd") or "").lower()
                if cmd in ("loginresponse", "initplayer"):
                    login_event.set()
                if cmd in queues:
                    queues[cmd].put_nowait(payload)

    reader_task = asyncio.create_task(read_loop())
    try:
        await send(writer, "Login", [CLIENT_KEY, username, token])
        try:
            await asyncio.wait_for(login_event.wait(), timeout=15)
        except asyncio.TimeoutError:
            print("[socket] no login acknowledgement in 15 seconds; proceeding")
        await send(writer, "firstJoin")
        await asyncio.sleep(1.0)

        quests = {}

        def absorb_quests():
            while not queues["getquests"].empty():
                response = queues["getquests"].get_nowait()
                for key, value in (response.get("quests") or {}).items():
                    if isinstance(value, dict):
                        quests[int(value.get("QuestID", key))] = value

        quest_ids = set(range(args.quest_start, args.quest_end + 1))
        if args.include_source_ids:
            quest_ids.update(source_quests)
        quest_ids = sorted(quest_ids)
        print(
            f"[quests] probing {len(quest_ids)} ids ({quest_ids[0]}..{quest_ids[-1]}) "
            f"in batches of {args.batch}"
        )
        quest_batches = list(chunks(quest_ids, args.batch))
        for number, batch_ids in enumerate(quest_batches, 1):
            await send(writer, "getQuests", [str(value) for value in batch_ids])
            await asyncio.sleep(args.delay)
            absorb_quests()
            if number == 1 or number == len(quest_batches) or number % 10 == 0:
                print(f"  quests sent through {batch_ids[-1]:>5} total={len(quests)}")
        await asyncio.sleep(args.settle)
        absorb_quests()
        print(f"  quests collection settled total={len(quests)}")

        apops = {}

        def absorb_apops():
            while not queues["getapop"].empty():
                response = queues["getapop"].get_nowait()
                for key, value in (response.get("apopData") or {}).items():
                    try:
                        apops[int(key)] = json.loads(value) if isinstance(value, str) else value
                    except (TypeError, ValueError, json.JSONDecodeError):
                        continue

        apop_ids = set(range(args.apop_start, args.apop_end + 1))
        if args.include_source_ids:
            apop_ids.update(source_apops)
        apop_ids = sorted(apop_ids)
        print(
            f"[apops] probing {len(apop_ids)} ids ({apop_ids[0]}..{apop_ids[-1]}) "
            f"in batches of {args.batch}"
        )
        apop_batches = list(chunks(apop_ids, args.batch))
        for number, batch_ids in enumerate(apop_batches, 1):
            await send(writer, "getApop", [",".join(map(str, batch_ids))])
            await asyncio.sleep(args.delay)
            absorb_apops()
            if number == 1 or number == len(apop_batches) or number % 10 == 0:
                print(f"  apops sent through {batch_ids[-1]:>5} total={len(apops)}")
        await asyncio.sleep(args.settle)
        absorb_apops()
        print(f"  apops collection settled total={len(apops)}")
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except (ConnectionError, OSError):
            pass
        reader_task.cancel()

    stamp = dt.date.today().strftime("%Y%m%d")
    suffix = f"_{args.output_tag}" if args.output_tag else ""
    quests_path = OUT / f"quests_live_{stamp}{suffix}.json"
    apops_path = OUT / f"apops_live_{stamp}{suffix}.json"
    quests_path.write_text(json.dumps(quests, indent=2), encoding="utf-8")
    apops_path.write_text(json.dumps(apops, indent=2), encoding="utf-8")
    print(f"[quests] snapshot={quests_path}")
    print(f"[apops] snapshot={apops_path}")

    quest_delta = compare("quests", quests, source_quests)
    apop_delta = compare("apops", apops, source_apops)
    delta_path = OUT / f"quests_apops_delta_{stamp}{suffix}.json"
    delta_path.write_text(json.dumps({
        "quests": quest_delta,
        "apops": apop_delta,
        "ranges": {
            "quests": [args.quest_start, args.quest_end],
            "apops": [args.apop_start, args.apop_end],
        },
    }, indent=2), encoding="utf-8")
    print(f"[delta] snapshot={delta_path}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--quest-start", type=int, default=1)
    parser.add_argument("--quest-end", type=int, default=400)
    parser.add_argument("--apop-start", type=int, default=1)
    parser.add_argument("--apop-end", type=int, default=300)
    parser.add_argument("--batch", type=int, default=100)
    parser.add_argument("--delay", type=float, default=0.1)
    parser.add_argument("--settle", type=float, default=5.0)
    parser.add_argument(
        "--include-source-ids",
        action="store_true",
        help="also request every ID already present in data/quests.json and data/apops.json",
    )
    parser.add_argument(
        "--output-tag",
        default="",
        help="optional safe filename suffix, such as tlapd_part2",
    )
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = parser.parse_args()
    if min(args.quest_start, args.apop_start, args.batch) < 1:
        parser.error("starts and batch must be positive")
    if args.quest_end < args.quest_start or args.apop_end < args.apop_start:
        parser.error("each end must be greater than or equal to its start")
    if args.output_tag and not args.output_tag.replace("_", "").replace("-", "").isalnum():
        parser.error("output tag may contain only letters, numbers, underscores, and hyphens")
    user, password = os.environ.get("AE_USER"), os.environ.get("AE_PASS")
    if not user or not password:
        sys.exit("set AE_USER and AE_PASS before running")
    asyncio.run(harvest(args, user, password))


if __name__ == "__main__":
    main()
