"""Survey Flash armor SWFs for conversion candidates: colour-custom (Base/Trim/Accessory) and animated parts.

usage: python3 scan_armors.py <classes dir, e.g. gamefiles/classes/M> > survey.tsv

For each SWF it finds the exported part symbols (<Link><G>Chest, Hip, Shoulder, Hand, Thigh, Shin, Foot, FootIdle,
Robe, RobeBack, Head) and walks every sprite reachable from each part. A part counts as animated when it reaches a
multi-frame sprite that has no AS3 class: classless timelines cannot carry stop() frame scripts, so they loop.
Classed multi-frame sprites are reported separately (they may be state clips that stop on frame 1).
"""
import lzma
import os
import struct
import sys
import zlib

import swfparse as sp

PARTS = ("Chest", "Hip", "Shoulder", "Hand", "Thigh", "Shin", "FootIdle", "Foot", "RobeBack", "Robe", "Head")


def part_of(name):
    for p in PARTS:  # FootIdle before Foot, RobeBack before Robe
        if name.endswith(p) and len(name) > len(p) + 1 and name[-len(p) - 1] in "MF":
            return p
    return None


def children(c):
    out = set()
    for f in c.frames or ():
        for pl in f.places:
            if pl.char is not None:
                out.add(pl.char)
    return out


def body_bytes(raw):
    if raw[:3] == b"CWS":
        return zlib.decompress(raw[8:])
    if raw[:3] == b"ZWS":
        return lzma.decompress(raw[12:17] + struct.pack("<Q", 0xFFFFFFFFFFFFFFFF) + raw[17:], format=lzma.FORMAT_ALONE)
    return raw


def scan(path):
    raw = open(path, "rb").read()
    m = sp.load(raw)
    body = body_bytes(raw)
    # ABC string pool entries are u30-length-prefixed; the armor colour slots (Skin/Hair/Eye are on every armor)
    cc = "".join(k for k, needle in (("B", b"\x04Base"), ("T", b"\x04Trim"), ("A", b"\x09Accessory"))
                 if b"mcSetColor" in body and needle in body)
    named = set(m.symbols)
    parts, anim_parts, max_frames, classed_multi = [], [], 1, 0
    for cid, name in m.symbols.items():
        p = part_of(name)
        if not p:
            continue
        parts.append(p)
        seen, stack, animated = set(), [cid], False
        while stack:
            k = stack.pop()
            if k in seen:
                continue
            seen.add(k)
            c = m.chars.get(k)
            if c is None or c.kind != "sprite":
                continue
            n = len(c.frames or ())
            if n > 1:
                if k in named:
                    classed_multi += 1
                else:
                    animated = True
                    max_frames = max(max_frames, n)
            stack.extend(children(c))
        if animated:
            anim_parts.append(p)
    return cc, sorted(parts), sorted(anim_parts), max_frames, classed_multi


def main():
    root = sys.argv[1]
    print("file\tbytes\tcc\tparts\tanimated_parts\tmax_frames\tclassed_multi")
    for f in sorted(os.listdir(root)):
        if not f.lower().endswith(".swf"):
            continue
        path = os.path.join(root, f)
        try:
            cc, parts, anim, mf, cm = scan(path)
        except Exception as e:  # noqa: BLE001
            print("%s\t%d\tERR\t%s\t\t\t" % (f, os.path.getsize(path), type(e).__name__), flush=True)
            continue
        print("%s\t%d\t%s\t%s\t%s\t%d\t%d" % (f, os.path.getsize(path), cc, ",".join(parts), ",".join(anim), mf, cm),
              flush=True)


if __name__ == "__main__":
    main()
