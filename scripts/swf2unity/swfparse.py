"""Minimal SWF reader for the Flash-to-Unity rig converter.

Reads exactly what the rig compiler needs, without an XML round-trip:
  - header (frame size, frame rate, root frame count)
  - every character definition's id, kind and bounds (shapes, morphs, sprites, bitmaps, text)
  - every timeline (root + DefineSprite): per-frame control tags
      PlaceObject/2/3 (depth, character, matrix, colour transform, ratio, clip depth, name, blend mode,
      filters present?, visibility), RemoveObject/2, FrameLabel, ShowFrame
  - SymbolClass (linkage names)
  - the raw bytes of every definition tag, so a synthetic render SWF can reuse them verbatim

Coordinates stay in twips (1/20 px). Colour transforms keep Flash's 8.8 fixed point (256 = 1.0).
"""
import lzma
import struct
import zlib
from dataclasses import dataclass, field
from typing import Dict, List, Optional


class Bits:
    def __init__(self, data: bytes, pos: int = 0):
        self.data, self.byte, self.bit = data, pos, 0

    def ub(self, n: int) -> int:
        v = 0
        for _ in range(n):
            if self.bit == 0:
                cur = self.data[self.byte]
            v = (v << 1) | ((self.data[self.byte] >> (7 - self.bit)) & 1)
            self.bit += 1
            if self.bit == 8:
                self.bit, self.byte = 0, self.byte + 1
        return v

    def sb(self, n: int) -> int:
        v = self.ub(n)
        if n and v & (1 << (n - 1)):
            v -= 1 << n
        return v

    def align(self) -> int:
        if self.bit:
            self.bit, self.byte = 0, self.byte + 1
        return self.byte


def read_rect(data: bytes, pos: int):
    b = Bits(data, pos)
    n = b.ub(5)
    xmin, xmax, ymin, ymax = b.sb(n), b.sb(n), b.sb(n), b.sb(n)
    return (xmin, xmax, ymin, ymax), b.align()


def read_matrix(data: bytes, pos: int):
    """Returns (a, b, c, d, tx, ty) with a/b/c/d as floats and tx/ty in twips, and the end position."""
    b = Bits(data, pos)
    a = d = 1.0
    bb = c = 0.0
    if b.ub(1):
        n = b.ub(5)
        a, d = b.sb(n) / 65536.0, b.sb(n) / 65536.0
    if b.ub(1):
        n = b.ub(5)
        bb, c = b.sb(n) / 65536.0, b.sb(n) / 65536.0
    n = b.ub(5)
    tx, ty = b.sb(n), b.sb(n)
    return (a, bb, c, d, tx, ty), b.align()


def read_cxform(data: bytes, pos: int, alpha: bool):
    """Returns ((mr, mg, mb, ma), (ar, ag, ab, aa)) in Flash fixed point and the end position."""
    b = Bits(data, pos)
    has_add, has_mult, n = b.ub(1), b.ub(1), b.ub(4)
    k = 4 if alpha else 3
    mult = [256, 256, 256, 256]
    add = [0, 0, 0, 0]
    if has_mult:
        mult[:k] = [b.sb(n) for _ in range(k)]
    if has_add:
        add[:k] = [b.sb(n) for _ in range(k)]
    return (tuple(mult), tuple(add)), b.align()


@dataclass
class Place:
    depth: int
    move: bool = False
    char: Optional[int] = None
    matrix: Optional[tuple] = None
    cxform: Optional[tuple] = None
    ratio: Optional[int] = None
    name: Optional[str] = None
    clip_depth: Optional[int] = None
    blend: Optional[int] = None
    has_filters: bool = False
    filters_raw: bytes = b""          # FILTERLIST bytes (count + filters), re-emitted verbatim
    visible: Optional[int] = None


FILTER_FIXED = {0: 23, 1: 9, 2: 15, 3: 27, 6: 80}  # DropShadow, Blur, Glow, Bevel, ColorMatrix


def skip_filters(body: bytes, p: int) -> int:
    """Skip a FILTERLIST and return the position after it."""
    n = body[p]
    p += 1
    for _ in range(n):
        fid = body[p]
        p += 1
        if fid in FILTER_FIXED:
            p += FILTER_FIXED[fid]
        elif fid in (4, 7):          # GradientGlow / GradientBevel
            colors = body[p]
            p += 1 + colors * 5 + 19
        elif fid == 5:               # Convolution
            mx, my = body[p], body[p + 1]
            p += 2 + 4 + 4 + 4 * mx * my + 4 + 1
        else:
            raise ValueError("unknown filter id %d" % fid)
    return p


class BitWriter:
    def __init__(self):
        self.out, self.cur, self.n = bytearray(), 0, 0

    def ub(self, v: int, bits: int):
        for i in range(bits - 1, -1, -1):
            self.cur = (self.cur << 1) | ((v >> i) & 1)
            self.n += 1
            if self.n == 8:
                self.out.append(self.cur)
                self.cur, self.n = 0, 0

    def sb(self, v: int, bits: int):
        self.ub(v & ((1 << bits) - 1), bits)

    def flush(self) -> bytes:
        if self.n:
            self.out.append(self.cur << (8 - self.n))
            self.cur, self.n = 0, 0
        return bytes(self.out)


def _nbits(*vals) -> int:
    n = 1
    for v in vals:
        need = (v.bit_length() if v >= 0 else (~v).bit_length()) + 1
        n = max(n, need)
    return n


def encode_matrix(m) -> bytes:
    a, b, c, d, tx, ty = m
    w = BitWriter()
    ia, id_, ib, ic = (int(round(x * 65536)) for x in (a, d, b, c))
    if (ia, id_) != (65536, 65536):
        w.ub(1, 1)
        n = _nbits(ia, id_)
        w.ub(n, 5); w.sb(ia, n); w.sb(id_, n)
    else:
        w.ub(0, 1)
    if (ib, ic) != (0, 0):
        w.ub(1, 1)
        n = _nbits(ib, ic)
        w.ub(n, 5); w.sb(ib, n); w.sb(ic, n)
    else:
        w.ub(0, 1)
    n = _nbits(int(tx), int(ty)) if (tx or ty) else 0
    w.ub(n, 5)
    if n:
        w.sb(int(tx), n); w.sb(int(ty), n)
    return w.flush()


def encode_cxform_alpha(cx) -> bytes:
    mult, add = cx
    has_mult = tuple(mult) != (256, 256, 256, 256)
    has_add = tuple(add) != (0, 0, 0, 0)
    w = BitWriter()
    w.ub(int(has_add), 1); w.ub(int(has_mult), 1)
    vals = (list(mult) if has_mult else []) + (list(add) if has_add else [])
    n = _nbits(*vals) if vals else 1
    w.ub(n, 4)
    if has_mult:
        for v in mult:
            w.sb(v, n)
    if has_add:
        for v in add:
            w.sb(v, n)
    return w.flush()


def tag(code: int, body: bytes) -> bytes:
    if len(body) < 0x3F and code not in (6, 21, 35, 20, 36, 90):
        return struct.pack("<H", (code << 6) | len(body)) + body
    return struct.pack("<HI", (code << 6) | 0x3F, len(body)) + body


def place_object3(depth: int, char: int, matrix=None, cxform=None, ratio=None, clip_depth=None,
                  blend=None, filters_raw: bytes = b"", name: Optional[str] = None) -> bytes:
    flags = 0x02  # has character (new placement)
    flags2 = 0
    body = bytearray()
    if matrix is not None:
        flags |= 0x04
    if cxform is not None:
        flags |= 0x08
    if ratio is not None:
        flags |= 0x10
    if name:
        flags |= 0x20
    if clip_depth:
        flags |= 0x40
    if filters_raw:
        flags2 |= 0x01
    if blend not in (None, 0, 1):
        flags2 |= 0x02
    body += bytes([flags, flags2]) + struct.pack("<HH", depth, char)
    if matrix is not None:
        body += encode_matrix(matrix)
    if cxform is not None:
        body += encode_cxform_alpha(cxform)
    if ratio is not None:
        body += struct.pack("<H", ratio)
    if name:
        body += name.encode("latin-1") + b"\0"
    if clip_depth:
        body += struct.pack("<H", clip_depth)
    if filters_raw:
        body += filters_raw
    if flags2 & 0x02:
        body += bytes([blend])
    return tag(70, bytes(body))


def encode_place(pl: "Place") -> bytes:
    """Re-encode any parsed placement as PlaceObject3, keeping move/modify semantics, filters and blend."""
    flags = 0x01 if pl.move else 0
    flags2 = 0
    body = bytearray()
    if pl.char is not None:
        flags |= 0x02
    if pl.matrix is not None:
        flags |= 0x04
    if pl.cxform is not None:
        flags |= 0x08
    if pl.ratio is not None:
        flags |= 0x10
    if pl.name:
        flags |= 0x20
    if pl.clip_depth:
        flags |= 0x40
    if pl.filters_raw:
        flags2 |= 0x01
    if pl.blend not in (None, 0, 1):
        flags2 |= 0x02
    if pl.visible == 0:
        flags2 |= 0x20
    body += bytes([flags, flags2]) + struct.pack("<H", pl.depth)
    if pl.char is not None:
        body += struct.pack("<H", pl.char)
    if pl.matrix is not None:
        body += encode_matrix(pl.matrix)
    if pl.cxform is not None:
        body += encode_cxform_alpha(pl.cxform)
    if pl.ratio is not None:
        body += struct.pack("<H", pl.ratio)
    if pl.name:
        body += pl.name.encode("latin-1") + b"\0"
    if pl.clip_depth:
        body += struct.pack("<H", pl.clip_depth)
    if pl.filters_raw:
        body += pl.filters_raw
    if flags2 & 0x02:
        body += bytes([pl.blend])
    if flags2 & 0x20:
        body += bytes([0, 0, 0, 0, 0])  # Visible = 0, BackgroundColor RGBA
    return tag(70, bytes(body))


def define_sprite(cid: int, frame_count: int, control_tags: bytes) -> bytes:
    """control_tags must not include the End tag; it is appended here."""
    return tag(39, struct.pack("<HH", cid, frame_count) + control_tags + b"\x00\x00")


@dataclass
class Frame:
    places: List[Place] = field(default_factory=list)
    removes: List[int] = field(default_factory=list)
    label: Optional[str] = None


@dataclass
class Character:
    id: int
    kind: str                       # shape | morph | sprite | bitmap | text | button | font | other
    tag: int
    bounds: Optional[tuple] = None  # twips (xmin, xmax, ymin, ymax); morph = start bounds
    end_bounds: Optional[tuple] = None
    frames: List[Frame] = field(default_factory=list)  # sprites only
    raw: bytes = b""                # full tag bytes (header + body) for re-emitting


@dataclass
class Movie:
    version: int
    stage: tuple
    fps: float
    frame_count: int
    chars: Dict[int, Character]
    root: List[Frame]
    symbols: Dict[int, str]
    raw_defs: List[bytes]           # definition tags in file order (for a synthetic render SWF)
    has_abc: bool = False


SHAPE_TAGS = {2: "shape", 22: "shape", 32: "shape", 83: "shape"}
MORPH_TAGS = {46: "morph", 84: "morph"}
BITMAP_TAGS = {6: "bitmap", 20: "bitmap", 21: "bitmap", 35: "bitmap", 36: "bitmap", 90: "bitmap"}
TEXT_TAGS = {11: "text", 33: "text", 37: "text"}
FONT_TAGS = {10: "font", 48: "font", 62: "font", 73: "font", 75: "font", 88: "font", 91: "font", 13: "font"}
BUTTON_TAGS = {7: "button", 34: "button"}
DEF_TAGS = {**SHAPE_TAGS, **MORPH_TAGS, **BITMAP_TAGS, **TEXT_TAGS, **FONT_TAGS, **BUTTON_TAGS, 39: "sprite",
            8: "jpegtables", 14: "sound", 18: "soundstream", 19: "soundstream", 45: "soundstream", 60: "video",
            87: "binary", 46: "morph"}


def iter_tags(data: bytes, pos: int, end: int):
    while pos + 2 <= end:
        start = pos
        code_len = struct.unpack_from("<H", data, pos)[0]
        pos += 2
        code, length = code_len >> 6, code_len & 0x3F
        if length == 0x3F:
            length = struct.unpack_from("<I", data, pos)[0]
            pos += 4
        yield code, data[pos:pos + length], data[start:pos + length]
        pos += length
        if code == 0:
            return


def parse_place(code: int, body: bytes) -> Place:
    if code == 4:  # PlaceObject
        char, depth = struct.unpack_from("<HH", body, 0)
        m, p = read_matrix(body, 4)
        cx = None
        if p < len(body):
            cx, p = read_cxform(body, p, alpha=False)
        return Place(depth=depth, char=char, matrix=m, cxform=cx)
    flags = body[0]
    p = 1
    flags2 = 0
    if code == 70:
        flags2 = body[1]
        p = 2
    depth = struct.unpack_from("<H", body, p)[0]
    p += 2
    pl = Place(depth=depth, move=bool(flags & 0x01))
    if code == 70 and (flags2 & 0x08 or (flags2 & 0x10 and flags & 0x02)):
        end = body.index(0, p)  # class name present
        p = end + 1
    if flags & 0x02:
        pl.char = struct.unpack_from("<H", body, p)[0]
        p += 2
    if flags & 0x04:
        pl.matrix, p = read_matrix(body, p)
    if flags & 0x08:
        pl.cxform, p = read_cxform(body, p, alpha=True)
    if flags & 0x10:
        pl.ratio = struct.unpack_from("<H", body, p)[0]
        p += 2
    if flags & 0x20:
        end = body.index(0, p)
        pl.name = body[p:end].decode("latin-1")
        p = end + 1
    if flags & 0x40:
        pl.clip_depth = struct.unpack_from("<H", body, p)[0]
        p += 2
    if code == 70:
        if flags2 & 0x01:
            pl.has_filters = True
            start = p
            p = skip_filters(body, p)
            pl.filters_raw = body[start:p]
        if flags2 & 0x02:
            pl.blend = body[p]
            p += 1
        if flags2 & 0x04:
            p += 1  # cacheAsBitmap
        if flags2 & 0x20 and p < len(body):
            pl.visible = body[p]
    return pl


def parse_timeline(data: bytes, pos: int, end: int) -> List[Frame]:
    frames, cur = [], Frame()
    for code, body, _ in iter_tags(data, pos, end):
        if code in (4, 26, 70):
            cur.places.append(parse_place(code, body))
        elif code == 5:
            cur.removes.append(struct.unpack_from("<H", body, 2)[0])
        elif code == 28:
            cur.removes.append(struct.unpack_from("<H", body, 0)[0])
        elif code == 43:
            cur.label = body.split(b"\0", 1)[0].decode("latin-1")
        elif code == 1:
            frames.append(cur)
            cur = Frame()
    return frames


def load(path_or_bytes) -> Movie:
    raw = path_or_bytes if isinstance(path_or_bytes, (bytes, bytearray)) else open(path_or_bytes, "rb").read()
    sig, version = raw[:3], raw[3]
    if sig == b"FWS":
        body = raw[8:]
    elif sig == b"CWS":
        body = zlib.decompress(raw[8:])
    elif sig == b"ZWS":
        body = lzma.decompress(raw[12:17] + struct.pack("<Q", 0xFFFFFFFFFFFFFFFF) + raw[17:], format=lzma.FORMAT_ALONE)
    else:
        raise ValueError("not a SWF")
    stage, p = read_rect(body, 0)
    fps = body[p + 1] + body[p] / 256.0
    frame_count = struct.unpack_from("<H", body, p + 2)[0]
    p += 4
    chars: Dict[int, Character] = {}
    symbols: Dict[int, str] = {}
    raw_defs: List[bytes] = []
    root_frames, cur = [], Frame()
    has_abc = False
    for code, tbody, traw in iter_tags(body, p, len(body)):
        if (code in DEF_TAGS or code in (39,)) and len(tbody) < 2:
            if code == 8:
                raw_defs.append(traw)  # an empty JPEGTables still precedes its DefineBits
            continue
        if code in DEF_TAGS or code in (39,):
            cid = struct.unpack_from("<H", tbody, 0)[0]
            kind = DEF_TAGS.get(code, "other")
            ch = Character(id=cid, kind=kind, tag=code, raw=traw)
            if kind in ("shape", "morph", "text"):
                ch.bounds, q = read_rect(tbody, 2)
                if kind == "morph":
                    ch.end_bounds, _ = read_rect(tbody, q)
            elif kind == "sprite":
                ch.frames = parse_timeline(tbody, 4, len(tbody))
            if kind not in ("jpegtables",):
                chars[cid] = ch
            raw_defs.append(traw)
        elif code in (56, 76):  # ExportAssets / SymbolClass
            n = struct.unpack_from("<H", tbody, 0)[0]
            q = 2
            for _ in range(n):
                cid = struct.unpack_from("<H", tbody, q)[0]
                e = tbody.index(0, q + 2)
                symbols[cid] = tbody[q + 2:e].decode("utf-8", "replace")
                q = e + 1
        elif code in (82, 72):
            has_abc = True
        elif code in (4, 26, 70):
            cur.places.append(parse_place(code, tbody))
        elif code in (5, 28):
            cur.removes.append(struct.unpack_from("<H", tbody, 2 if code == 5 else 0)[0])
        elif code == 43:
            cur.label = tbody.split(b"\0", 1)[0].decode("latin-1")
        elif code == 1:
            root_frames.append(cur)
            cur = Frame()
        elif code in (8, 9, 69, 77, 86, 1023):
            raw_defs.append(traw) if code == 8 else None  # JPEGTables must accompany DefineBits
    return Movie(version=version, stage=stage, fps=fps, frame_count=frame_count, chars=chars, root=root_frames,
                 symbols=symbols, raw_defs=raw_defs, has_abc=has_abc)


def display_lists(frames: List[Frame]):
    """Yield, per frame, {depth: state} where state is a dict of the active placement's properties.
    Implements Flash display-list semantics: PlaceObject(move=False) replaces, move=True modifies,
    a new character at a moved depth replaces the instance (new instance id)."""
    active: Dict[int, dict] = {}
    serial = 0
    out = []
    for fr in frames:
        for d in fr.removes:
            active.pop(d, None)
        for pl in fr.places:
            st = active.get(pl.depth)
            if not pl.move or st is None or (pl.char is not None and pl.char != st["char"]):
                serial += 1
                st = {"inst": serial, "char": pl.char if pl.char is not None else (st or {}).get("char"),
                      "matrix": (1.0, 0.0, 0.0, 1.0, 0, 0), "cxform": None, "ratio": 0, "clip": None,
                      "blend": None, "name": None, "filters": False, "filters_raw": b""}
                if not pl.move and active.get(pl.depth) and pl.char is None:
                    st.update({k: v for k, v in active[pl.depth].items() if k != "inst"})
            else:
                st = dict(st)
            if pl.matrix is not None:
                st["matrix"] = pl.matrix
            if pl.cxform is not None:
                st["cxform"] = pl.cxform
            if pl.ratio is not None:
                st["ratio"] = pl.ratio
            if pl.clip_depth is not None:
                st["clip"] = pl.clip_depth
            if pl.blend is not None:
                st["blend"] = pl.blend
            if pl.name is not None:
                st["name"] = pl.name
            if pl.has_filters:
                st["filters"] = True
                st["filters_raw"] = pl.filters_raw
            active[pl.depth] = st
        out.append({d: dict(s) for d, s in active.items()})
    return out
