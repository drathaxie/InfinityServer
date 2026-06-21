#!/usr/bin/env python3
"""
Pull a clean, ordered login handshake out of the live packets.jsonl capture.
We find the first 'Login' c2s request and emit every packet (both directions)
from there until movement begins, so the server has a real script to replay.

Output:
  capture/handshake.json      ordered [{dir, cmd, pkt}] for the login->play window
  capture/cmd_inventory.json  {responses:{cmd:count}, requests:{cmd:count}}
  capture/samples/<cmd>.json  one representative payload per distinct s2c cmd
"""
import json, pathlib, collections, sys

CAP = pathlib.Path(r"C:\Program Files (x86)\Steam\steamapps\common"
                   r"\AdventureQuest Worlds Unity Playtest\UserData\Beyond\packets.jsonl")
HERE = pathlib.Path(__file__).resolve().parent
SAMPLES = HERE / "samples"
SAMPLES.mkdir(exist_ok=True)

# Commands that mark "we're now in normal play" — stop the handshake window here.
PLAY_MARKERS = {"mv", "movement", "Attack"}

def cmd_of(pkt):
    return pkt.get("Cmd") or pkt.get("cmd") or "(none)"

def main():
    if not CAP.exists():
        print("capture not found:", CAP); sys.exit(1)

    resp_counts = collections.Counter()
    req_counts = collections.Counter()
    samples = {}

    handshake = []
    capturing = False
    movement_seen = 0

    with CAP.open(encoding="utf-8", errors="replace") as f:
        for line in f:
            try:
                o = json.loads(line)
            except Exception:
                continue
            d = o.get("dir"); pkt = o.get("pkt") or {}
            c = cmd_of(pkt)
            if d == "s2c":
                resp_counts[c] += 1
                if c not in samples:
                    samples[c] = pkt
            elif d == "c2s":
                req_counts[c] += 1

            # Begin the handshake window at the first Login request.
            if not capturing and d == "c2s" and c == "Login":
                capturing = True
            if capturing:
                handshake.append({"dir": d, "cmd": c, "pkt": pkt})
                if c in PLAY_MARKERS:
                    movement_seen += 1
                    # Capture a little movement then stop.
                    if movement_seen >= 8:
                        break

    (HERE / "handshake.json").write_text(
        json.dumps(handshake, indent=2), encoding="utf-8")
    (HERE / "cmd_inventory.json").write_text(
        json.dumps({"responses": dict(resp_counts.most_common()),
                    "requests": dict(req_counts.most_common())}, indent=2),
        encoding="utf-8")
    for c, pkt in samples.items():
        safe = "".join(ch if ch.isalnum() else "_" for ch in c)
        (SAMPLES / f"{safe}.json").write_text(
            json.dumps(pkt, indent=2), encoding="utf-8")

    print(f"handshake packets: {len(handshake)}")
    print(f"distinct s2c: {len(resp_counts)}  distinct c2s: {len(req_counts)}")
    print("handshake cmd sequence:")
    for h in handshake[:40]:
        arrow = ">>" if h["dir"] == "c2s" else "<<"
        print(f"   {arrow} {h['cmd']}")

if __name__ == "__main__":
    main()
