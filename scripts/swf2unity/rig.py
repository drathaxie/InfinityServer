"""Decompose a Flash symbol (an armor part, a weapon, a pet...) into a small Unity-friendly rig.

Painter-order walk of the symbol's display tree:
  - a subtree with no multi-frame sprite inside is *static*: it is drawn once and baked into an image;
  - a single-frame container whose subtree animates is expanded into its children (an outer glow/shadow on such a
    container is kept as a static "knockout" layer: the filter alone, drawn under the children);
  - a multi-frame sprite (it loops on its own) becomes one *track* per display-list instance: a static child whose
    matrix / colour transform / presence change per frame;
  - it becomes a *flipbook* instead (every frame rendered as an image, identical frames shared) when tracks cannot
    express it: frame-by-frame swaps at a depth, morphs, nested animation, or filters / a blend mode on the
    animated instance itself (those must be rendered with its content).
Consecutive static draws merge into one *band*; a draw with a blend mode starts its own band so the shader can
blend it onto the layers below, as Flash does. The output layer list is what the FlashRig shader composites.
All matrices are in the symbol's local twips.
"""
import math
import struct
from dataclasses import dataclass, field
from typing import List, Optional

import swfparse as sp
from render import IDENT, cx_mul, mat_mul

BLEND_NAMES = {2: "layer", 3: "multiply", 4: "screen", 5: "lighten", 6: "darken", 7: "difference", 8: "add",
               9: "subtract", 10: "invert", 11: "alpha", 12: "erase", 13: "overlay", 14: "hardlight"}
SHADER_BLENDS = {3, 4, 5, 6, 7, 8, 9, 13, 14}  # implemented by FlashRig; others draw as normal
MAX_PERIOD = 960


@dataclass
class Item:
    char: int
    matrix: tuple
    cxform: Optional[tuple] = None
    blend: Optional[int] = None
    filters_raw: bytes = b""
    clip: Optional[int] = None     # clip depth in the parent (masks), only meaningful inside one band
    depth: int = 0
    glow: bool = False             # a container's knockout outer glow (soft: stored at half resolution)


@dataclass
class Layer:
    kind: str                       # band | track | flipbook
    items: List[Item] = field(default_factory=list)   # band: what to bake; track/flipbook: the rendered instance
    period: int = 1
    frames: List[dict] = field(default_factory=list)  # track/flipbook: per frame {"m", "cx", "vis"}
    sprite: Optional[int] = None    # flipbook: the animated sprite
    blend: Optional[int] = None     # blend onto the layers below (shader), None/0/1 = normal
    notes: List[str] = field(default_factory=list)
    render_frames: int = 0          # flipbook: frames to render (0 = period); a nested clip renders its own loop
    frame_src: Optional[List[int]] = None  # flipbook: layer frame -> rendered frame (None = identity)
    group_start: bool = False
    group_end: int = -1


def is_normal(blend):
    return blend in (None, 0, 1, 2)


def knockout_outer_filters(filters_raw):
    """Keep only outer glows / drop shadows from a FILTERLIST, with knockout set (the effect without the source).
    Returns b"" when there is nothing to keep."""
    if not filters_raw:
        return b""
    n, p, kept = filters_raw[0], 1, []
    for _ in range(n):
        fid = filters_raw[p]
        size = sp.FILTER_FIXED.get(fid)
        if size is None:
            return b""  # variable-length filters: give up rather than mis-parse
        body = bytearray(filters_raw[p + 1:p + 1 + size])
        p += 1 + size
        if fid == 2 and not body[14] & 0x80:      # Glow: flags byte after colour, blurs, strength
            body[14] |= 0x40
            kept.append(bytes([fid]) + bytes(body))
        elif fid == 0 and not body[22] & 0x80:    # DropShadow: flags byte last
            body[22] |= 0x40
            kept.append(bytes([fid]) + bytes(body))
    return bytes([len(kept)]) + b"".join(kept) if kept else b""


class Rig:
    def __init__(self, movie, expanded=False, cc=None, faithful_effects=False):
        self.m = movie
        self._anim = {}
        self._period = {}
        self._context = {}
        self.notes = []
        self.expanded = expanded
        self.faithful_effects = faithful_effects
        self.cc = cc if cc is not None else {}

    def static_symbol(self, items, color=None):
        """A render-only container retains painter order and inherited color calls."""
        cid = max(self.m.chars) + 1
        places = [sp.Place(depth=i + 1, char=it.char, matrix=it.matrix, cxform=it.cxform,
                           blend=it.blend, filters_raw=it.filters_raw, clip_depth=it.clip)
                  for i, it in enumerate(items)]
        raw = sp.define_sprite(cid, 1, b''.join(sp.encode_place(p) for p in places) + sp.tag(1, b''))
        self.m.chars[cid] = sp.Character(cid, 'sprite', 39, frames=[sp.Frame(places=places)], raw=raw)
        self.m.raw_defs.append(raw)
        if color is not None:
            self.cc[cid] = color
        return cid

    def animated(self, cid, stack=()):
        """True when the character's subtree contains a multi-frame sprite."""
        if cid in self._anim:
            return self._anim[cid]
        ch = self.m.chars.get(cid)
        res = False
        if ch is not None and ch.kind == "sprite" and cid not in stack:
            if len(ch.frames) > 1:
                res = True
            else:
                for fr in ch.frames:
                    for pl in fr.places:
                        if pl.char is not None and self.animated(pl.char, stack + (cid,)):
                            res = True
        self._anim[cid] = res
        return res

    def period(self, cid, stack=()):
        """Loop length of a sprite's appearance: its own frame count and every nested animated sprite's (LCM)."""
        if cid in self._period:
            return self._period[cid]
        ch = self.m.chars.get(cid)
        p = 1
        if ch is not None and ch.kind == "sprite" and cid not in stack:
            p = max(1, len(ch.frames))
            for fr in ch.frames:
                for pl in fr.places:
                    if pl.char is not None and self.animated(pl.char):
                        q = self.period(pl.char, stack + (cid,))
                        p = p * q // math.gcd(p, q)
        self._period[cid] = min(p, MAX_PERIOD)
        if p > MAX_PERIOD:
            self.notes.append("#%d loops every %d frames; capped at %d" % (cid, p, MAX_PERIOD))
        return self._period[cid]

    def backdrop_dependent(self, cid, stack=()):
        """Static normal containers cannot isolate descendants' blend backdrops."""
        if cid in self._context:
            return self._context[cid]
        ch = self.m.chars.get(cid)
        result = False
        if ch is not None and ch.kind == 'sprite' and cid not in stack:
            result = any(not is_normal(pl.blend) or self.backdrop_dependent(pl.char, stack + (cid,))
                         for frame in ch.frames for pl in frame.places if pl.char is not None)
        self._context[cid] = result
        return result

    def decompose(self, cid):
        draws = []
        self._walk(cid, IDENT, None, None, b"", None, draws, 0)
        layers = []
        for d in draws:
            if isinstance(d, Item):
                blend = d.blend if d.blend in SHADER_BLENDS else None
                if d.blend not in (None, 0, 1) and blend is None:
                    self.notes.append("#%d uses %s blend; drawn normally" % (d.char, BLEND_NAMES.get(d.blend, d.blend)))
                last = layers[-1] if layers else None
                # Normal source-over draws are associative. Flattening several
                # additive/multiply draws with source-over first changes their
                # overlap before the shader applies the blend to the backdrop.
                if blend is None and last is not None and last.kind == "band" and last.blend is None:
                    last.items.append(d)
                else:
                    layers.append(Layer(kind="band", items=[d], blend=blend))
            else:
                layers.append(d)
        for L in layers:  # the band render itself must not blend: the shader does it against the layers below
            if L.kind == "band" and L.blend is not None:
                for it in L.items:
                    it.blend = None
        return layers

    def _walk(self, cid, M, CX, blend, filters, own_cx, out, depth, inherited_cx=None):
        """M/CX: accumulated matrix and colour transform including this instance's own placement.
        own_cx: this instance's own colour transform (baked into flipbook renders together with its filters)."""
        if self.expanded and cid in self.cc:
            if not self.animated(cid):
                # Keep a static color-custom clip intact. The color call belongs
                # inside an uncolored wrapper, while its parent's transform stays
                # outside. Expanding it would apply color at the wrong level.
                wrapper = self.static_symbol([Item(cid, IDENT)])
                out.append(Item(char=wrapper, matrix=M, cxform=inherited_cx, blend=blend,
                                filters_raw=filters, depth=depth))
                return
            # A decomposed child must retain the color call of every enclosing clip.
            # Keep it on a render-only wrapper instead of baking the authored red CX.
            color = self.cc.pop(cid)
            first = len(out)
            self._walk(cid, M, None, blend, filters, None, out, depth)
            self.cc[cid] = color
            for draw in out[first:]:
                if isinstance(draw, Item):
                    colored = self.static_symbol([Item(draw.char, IDENT, cxform=draw.cxform,
                                                       filters_raw=draw.filters_raw)], color)
                    # The color call replaces this clip's own transform, not
                    # the containing clip's fade/tint. A non-color wrapper keeps
                    # that outer transform separate in the synthetic render.
                    draw.char = self.static_symbol([Item(colored, IDENT)])
                    draw.cxform = inherited_cx
                    draw.filters_raw = b''
                else:
                    it = draw.items[0]
                    colored = self.static_symbol([Item(it.char, IDENT, cxform=it.cxform,
                                                        filters_raw=it.filters_raw)], color)
                    it.char = self.static_symbol([Item(colored, IDENT)])
                    for frame in draw.frames:
                        frame['cx'] = cx_mul(inherited_cx, frame['cx'])
                    it.filters_raw, it.cxform = b'', None
                    if draw.kind == 'flipbook':
                        draw.sprite = it.char
            return
        ch = self.m.chars.get(cid)
        if ch is None or ch.kind != "sprite" or (not self.animated(cid) and
                not (self.expanded and self.backdrop_dependent(cid))):
            out.append(Item(char=cid, matrix=M, cxform=CX, blend=blend, filters_raw=filters, depth=depth))
            return
        dl = sp.display_lists(ch.frames)
        if len(dl) == 1:
            # Bake the animated subtree with its actual filters/masks. Effects
            # must follow the artwork rather than stay frozen at frame zero.
            masked = any(st.get('clip') for st in dl[0].values())
            if self.faithful_effects and (filters or masked or not is_normal(blend)):
                period = self.period(cid)
                if self.notes:
                    raise ValueError('Effect subtree needs an uncapped clock: ' + repr(self.notes))
                out.append(Layer(kind='flipbook', sprite=cid, period=period,
                    frames=[{'m': M, 'cx': CX, 'vis': True} for _ in range(period)],
                    items=[Item(char=cid, matrix=IDENT, filters_raw=filters)],
                    blend=blend if blend in SHADER_BLENDS else None))
                return
            if not is_normal(blend):
                self.notes.append("container #%d has %s blend but animates; drawn normally" % (
                    cid, BLEND_NAMES.get(blend, blend)))
            if filters:
                glow = knockout_outer_filters(filters)
                if glow:  # the container's outer glow/shadow, from its frame-0 content, under its children
                    out.append(Item(char=cid, matrix=M, cxform=CX, filters_raw=glow, depth=depth, glow=True))
                    self.notes.append("container #%d filter kept as a static glow layer" % cid)
                    if self.expanded and glow == filters:
                        # An authored knockout filter draws only the effect. Adding
                        # the original children here duplicates the source artwork.
                        return
                else:
                    self.notes.append("container #%d has filters but animates; drawn without them" % cid)
            for d, st in sorted(dl[0].items()):
                if st.get("clip"):
                    if any(self.animated(s["char"]) for dd, s in dl[0].items() if d < dd <= st["clip"]):
                        self.notes.append("mask at #%d d%d covers animated content; mask ignored" % (cid, d))
                        continue
                    # a static mask over static siblings: keep it with them so the band render clips them
                    out.append(Item(char=st["char"], matrix=mat_mul(M, st["matrix"] or IDENT), cxform=CX,
                                    clip=st["clip"], depth=d))
                    continue
                self._walk(st["char"], mat_mul(M, st["matrix"] or IDENT), cx_mul(CX, st["cxform"]),
                           st.get("blend"), st.get("filters_raw") or b"", st["cxform"], out, d, inherited_cx=CX)
            return
        n = len(dl)
        insts, depth_insts = {}, {}
        for fi, state in enumerate(dl):
            for d, st in state.items():
                insts.setdefault(st["inst"], {"depth": d, "frames": {}})["frames"][fi] = st
                depth_insts.setdefault(d, set()).add(st["inst"])
        reasons = []
        nested = set()  # instances that move an animated child: a flipbook of the child + animated transform
        grouped = self.expanded and not is_normal(blend) and not filters
        if filters or (not is_normal(blend) and not grouped):
            reasons.append("instance filters/blend")
        if not self.expanded and any(len(v) > 1 for v in depth_insts.values()):
            reasons.append("frame-by-frame swaps")
        for inst_id, inst in insts.items():
            chars = {st["char"] for st in inst["frames"].values()}
            c0 = self.m.chars.get(next(iter(chars)))
            if len(chars) != 1:
                reasons.append("character swaps")
            elif self.animated(next(iter(chars))):
                if any(st.get("clip") or st.get("blend") not in (None, 0, 1) for st in inst["frames"].values()):
                    reasons.append("nested animation with mask/blend")
                else:
                    nested.add(inst_id)
            elif c0 is not None and c0.kind == "morph" and len({st.get("ratio") for st in inst["frames"].values()}) > 1:
                reasons.append("morph")
            elif any(st.get("clip") for st in inst["frames"].values()):
                reasons.append("animated mask")
        if reasons:
            # parent transform/colour stay in the shader; the instance's own colour + filters are rendered
            parent_cx = CX
            if own_cx is not None:
                parent_cx = None if CX == own_cx else CX  # best effort: own cx is baked, the rest is shader-side
            frames = [{"m": M, "cx": parent_cx, "vis": True} for _ in range(self.period(cid))]
            lay = Layer(kind="flipbook", sprite=cid, period=self.period(cid), frames=frames,
                        items=[Item(char=cid, matrix=IDENT, cxform=own_cx, filters_raw=filters)],
                        blend=blend if blend in SHADER_BLENDS else None)
            lay.notes.append(", ".join(sorted(set(reasons))))
            out.append(lay)
            return
        first_output = len(out)
        for inst_id, inst in sorted(insts.items(), key=lambda kv: (kv[1]["depth"], kv[0])):
            first = min(inst["frames"])
            st0 = inst["frames"][first]
            if self.expanded and inst_id in nested and not st0.get('filters_raw'):
                children = self.decompose(st0['char'])
                for child in children:
                    if child.kind == 'band':
                        child = Layer(kind='track', items=[Item(self.static_symbol(child.items), IDENT)],
                                      frames=[{'m': IDENT, 'cx': None, 'vis': True}], blend=child.blend)
                    total = math.lcm(n, child.period)
                    if total > MAX_PERIOD:
                        raise ValueError('Expanded track period exceeds configured limit: %d' % total)
                    poses, sources = [], []
                    for fi in range(total):
                        outer = inst['frames'].get(fi % n)
                        inner_f = (fi - first) % child.period
                        inner_pose = child.frames[inner_f]
                        poses.append({'m': mat_mul(mat_mul(M, (outer or st0)['matrix'] or IDENT), inner_pose['m']),
                                      'cx': cx_mul(cx_mul(CX, (outer or st0)['cxform']), inner_pose['cx']),
                                      'vis': outer is not None and inner_pose['vis']})
                        sources.append(child.frame_src[inner_f] if child.frame_src else inner_f)
                    out.append(Layer(kind=child.kind, items=child.items, sprite=child.sprite, period=total,
                                     frames=poses, blend=child.blend, render_frames=child.render_frames or child.period,
                                     frame_src=sources if child.kind == 'flipbook' else None,
                                     group_start=child.group_start, group_end=child.group_end))
                continue
            inner = self.period(st0["char"]) if inst_id in nested else 1
            total = n * inner // math.gcd(n, inner)
            if total > MAX_PERIOD:
                self.notes.append("#%d in #%d loops every %d frames; capped" % (st0["char"], cid, total))
                total = MAX_PERIOD
            frames = []
            for fi in range(total):
                st = inst["frames"].get(fi % n)
                if st is None:
                    frames.append({"m": mat_mul(M, st0["matrix"] or IDENT), "cx": None, "vis": False})
                else:
                    frames.append({"m": mat_mul(M, st["matrix"] or IDENT), "cx": cx_mul(CX, st["cxform"]),
                                   "vis": True})
            item = Item(char=st0["char"], matrix=IDENT, filters_raw=st0.get("filters_raw") or b"")
            tb = st0.get("blend")
            if inst_id in nested:
                # the child plays its own loop from the frame it was placed on; the parent only moves it
                lay = Layer(kind="flipbook", sprite=st0["char"], period=total, frames=frames, items=[item],
                            blend=tb if tb in SHADER_BLENDS else None)
                lay.render_frames = inner
                lay.frame_src = [(fi - first) % inner for fi in range(total)]
                lay.notes.append("nested clip moved by #%d" % cid)
                out.append(lay)
                continue
            out.append(Layer(kind="track", items=[item], period=total, frames=frames,
                             blend=tb if tb in SHADER_BLENDS else None))
        if grouped and len(out) > first_output:
            if out[first_output].group_start or out[-1].group_end >= 0:
                raise ValueError('Nested shader blend groups need a deeper group stack')
            out[first_output].group_start = True
            out[-1].group_end = blend or 0


def describe(layers):
    lines = []
    for i, l in enumerate(layers):
        b = (" blend=" + BLEND_NAMES.get(l.blend, str(l.blend))) if l.blend else ""
        if l.kind == "band":
            lines.append("%d band  items=%s%s" % (i, ",".join("#%d" % it.char for it in l.items), b))
        else:
            vis = sum(1 for f in l.frames if f["vis"])
            lines.append("%d %-8s #%d period=%d visible=%d%s %s" % (
                i, l.kind, l.sprite if l.kind == "flipbook" else l.items[0].char, l.period, vis, b,
                ("(" + "; ".join(l.notes) + ")") if l.notes else ""))
    return "\n".join(lines)
