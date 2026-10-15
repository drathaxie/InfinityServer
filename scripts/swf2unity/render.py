"""Render many small pieces of a SWF in one JPEXS pass by building a synthetic render SWF.

A job is a list of placements (character, matrix, colour transform, blend, filters) in a shared local space,
plus an optional colour-custom variant ({location: (r, g, b)}) applied to every mcSetColor clip inside it the way
AvatarMC.changeColor does. Each job becomes one root frame. All jobs of one zoom go into one SWF, so a whole
armor renders with a handful of JVM starts. Images come back cropped, with the job-local twips position of their
top-left pixel, so callers can place them exactly.
"""
import glob
import os
import re
import struct
import math
from collections.abc import Sequence

from PIL import Image

import swfparse as sp
from ccscripts import ffdec, shade_offsets

IDENT = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
NO_CX = ((256, 256, 256, 256), (0, 0, 0, 0))


class CroppedFrames(Sequence):
    """Decode a cached frame only while a caller needs it, without retaining the stage."""
    def __init__(self, paths, boxes):
        self.paths, self.boxes = paths, boxes

    def __len__(self):
        return len(self.paths)

    def __getitem__(self, index):
        if isinstance(index, slice):
            return [self[i] for i in range(*index.indices(len(self)))]
        with Image.open(self.paths[index]) as source:
            with source.convert('RGBA') as rgba:
                return rgba.crop(self.boxes[index])


def mat_mul(m1, m2):
    """m1 after m2 (Flash: a child placed with m2 inside a parent placed with m1 has world matrix m1·m2)."""
    a1, b1, c1, d1, x1, y1 = m1
    a2, b2, c2, d2, x2, y2 = m2
    return (a1 * a2 + c1 * b2, b1 * a2 + d1 * b2, a1 * c2 + c1 * d2, b1 * c2 + d1 * d2,
            a1 * x2 + c1 * y2 + x1, b1 * x2 + d1 * y2 + y1)


def mat_apply(m, x, y):
    a, b, c, d, tx, ty = m
    return a * x + c * y + tx, b * x + d * y + ty


def mat_inv(m):
    a, b, c, d, tx, ty = m
    det = a * d - b * c
    ia, ib, ic, id_ = d / det, -b / det, -c / det, a / det
    return (ia, ib, ic, id_, -(ia * tx + ic * ty), -(ib * tx + id_ * ty))


def cx_mul(outer, inner):
    """Concatenate colour transforms (inner applied first), both ((mr,mg,mb,ma),(ar,ag,ab,aa)) in 8.8/units."""
    if outer is None:
        return inner
    if inner is None:
        return outer
    (om, oa), (im, ia) = outer, inner
    mult = tuple(int(round(o * i / 256)) for o, i in zip(om, im))
    add = tuple(int(round(o * a / 256 + b)) for o, a, b in zip(om, ia, oa))
    return mult, add


class Geometry:
    """Frame-0 bounds of characters in twips, following nested placements (filters not included)."""

    def __init__(self, movie):
        self.m = movie
        self.cache = {}

    def bounds(self, cid, frame=0):
        key = (cid, frame)
        if key in self.cache:
            return self.cache[key]
        self.cache[key] = None  # recursion guard
        ch = self.m.chars.get(cid)
        out = None
        if ch is not None and ch.kind in ("shape", "text"):
            out = ch.bounds
        elif ch is not None and ch.kind == "morph":
            b0, b1 = ch.bounds, ch.end_bounds or ch.bounds
            out = (min(b0[0], b1[0]), max(b0[1], b1[1]), min(b0[2], b1[2]), max(b0[3], b1[3]))
        elif ch is not None and ch.kind == "sprite" and ch.frames:
            dl = sp.display_lists(ch.frames)
            state = dl[min(frame, len(dl) - 1)]
            for d, st in state.items():
                if st.get("clip"):
                    continue  # mask shapes do not draw
                b = self.bounds(st["char"])
                if b is None:
                    continue
                out = union(out, transform_bounds(st["matrix"] or IDENT, b))
        self.cache[key] = out
        return out

    def timeline_bounds(self, cid, stack=()):
        """Conservative envelope of every nested timeline, including moving children."""
        key = (cid, 'timeline')
        if key in self.cache:
            return self.cache[key]
        if cid in stack:
            return None
        ch = self.m.chars.get(cid)
        if ch is None or ch.kind != 'sprite':
            return self.bounds(cid)
        out = None
        for state in sp.display_lists(ch.frames):
            for st in state.values():
                if st.get('clip'):
                    continue
                b = self.timeline_bounds(st['char'], stack + (cid,))
                if b is not None:
                    out = union(out, transform_bounds(st['matrix'] or IDENT, b))
        self.cache[key] = out
        return out


def transform_bounds(m, b):
    pts = [mat_apply(m, x, y) for x in (b[0], b[1]) for y in (b[2], b[3])]
    return (min(p[0] for p in pts), max(p[0] for p in pts), min(p[1] for p in pts), max(p[1] for p in pts))


def union(a, b):
    if a is None:
        return b
    if b is None:
        return a
    return (min(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), max(a[3], b[3]))


def tag_body(raw):
    code_len = struct.unpack_from("<H", raw, 0)[0]
    return raw[6:] if (code_len & 0x3F) == 0x3F else raw[2:]


def cc_cxform(loc_shade, variant):
    loc, shade = loc_shade
    r, g, b = variant.get(loc, (0, 0, 0))
    o = shade_offsets(loc, shade)
    clamp = lambda v: max(-255, min(255, v))  # noqa: E731
    return (0, 0, 0, 256), (clamp(r + o[0]), clamp(g + o[1]), clamp(b + o[2]), 0)


class RenderSwf:
    """Builds one synthetic SWF: the source definitions, per-variant clones of colour-custom sprites, one wrapper
    sprite per job and a root timeline showing one job per frame."""

    PAD_PX = 48  # room for glows/blurs around each job

    def __init__(self, movie, cc):
        self.m = movie
        self.cc = cc  # {char id: (location, shade)}
        self.geo = Geometry(movie)
        self.next_id = max(movie.chars) + 1
        self.extra_defs = []
        self.variant_maps = {}
        self._contains_cc = {}

    def contains_cc(self, cid, stack=()):
        if cid in self._contains_cc:
            return self._contains_cc[cid]
        ch = self.m.chars.get(cid)
        res = False
        if ch is not None and ch.kind == "sprite" and cid not in stack:
            for fr in ch.frames:
                for pl in fr.places:
                    if pl.char is not None and (pl.char in self.cc or self.contains_cc(pl.char, stack + (cid,))):
                        res = True
        self._contains_cc[cid] = res
        return res

    def variant_map(self, vkey, variant):
        """Clone every sprite that (transitively) places a colour-custom clip, overriding those placements."""
        if vkey in self.variant_maps:
            return self.variant_maps[vkey]
        cmap = {}
        order = []

        def visit(cid, stack):
            if cid in cmap or cid in stack:
                return
            ch = self.m.chars.get(cid)
            if ch is None or ch.kind != "sprite" or not self.contains_cc(cid):
                return
            for fr in ch.frames:
                for pl in fr.places:
                    if pl.char is not None:
                        visit(pl.char, stack | {cid})
            cmap[cid] = self.next_id
            self.next_id += 1
            order.append(cid)

        for cid in list(self.m.chars):
            visit(cid, frozenset())
        for cid in order:  # children are cloned before their parents
            self.extra_defs.append(self._rewrite_sprite(cid, cmap[cid], cmap, variant))
        self.variant_maps[vkey] = cmap
        return cmap

    def _rewrite_sprite(self, cid, new_id, cmap, variant):
        body = tag_body(self.m.chars[cid].raw)
        frame_count = struct.unpack_from("<H", body, 2)[0]
        out = bytearray()
        depth_char = {}
        for code, tbody, traw in sp.iter_tags(body, 4, len(body)):
            if code == 0:
                break
            if code in (4, 26, 70):
                pl = sp.parse_place(code, tbody)
                if pl.char is not None:
                    depth_char[pl.depth] = pl.char
                cur = depth_char.get(pl.depth)
                changed = False
                if pl.char is not None and pl.char in cmap:
                    pl.char = cmap[pl.char]
                    changed = True
                if cur in self.cc and (pl.char is not None or pl.cxform is not None):
                    pl.cxform = cc_cxform(self.cc[cur], variant)
                    changed = True
                out += sp.encode_place(pl) if changed else traw
            elif code in (5, 28):
                d = struct.unpack_from("<H", tbody, 2 if code == 5 else 0)[0]
                depth_char.pop(d, None)
                out += traw
            else:
                out += traw
        return sp.define_sprite(new_id, frame_count, bytes(out))

    def job_bounds(self, items):
        b = None
        for it in items:
            cb = self.geo.timeline_bounds(it["char"])
            if cb is not None:
                b = union(b, transform_bounds(it.get("matrix") or IDENT, cb))
        return b or (0, 20, 0, 20)

    def build(self, jobs):
        """jobs: [{"items": [...], "variant": {loc: rgb} or None, "vkey": str}] -> (swf bytes, per-job layout)."""
        pad = self.PAD_PX * 20
        layouts, wrappers = [], []
        max_w = max_h = 0
        for job in jobs:
            b = self.job_bounds(job["items"])
            cmap = self.variant_map(job["vkey"], job["variant"]) if job.get("variant") is not None else {}
            tags = bytearray()
            for i, it in enumerate(job["items"]):
                char = cmap.get(it["char"], it["char"])
                cx = it.get("cxform")
                if job.get("variant") is not None and it["char"] in self.cc:
                    cx = cc_cxform(self.cc[it["char"]], job["variant"])
                pl = sp.Place(depth=i + 1, char=char, matrix=it.get("matrix") or IDENT, cxform=cx,
                              blend=it.get("blend"), filters_raw=it.get("filters_raw") or b"",
                              clip_depth=it.get("clip"))
                tags += sp.encode_place(pl)
            tags += sp.tag(1, b"")
            wid = self.next_id
            self.next_id += 1
            wrappers.append(sp.define_sprite(wid, 1, bytes(tags)))
            w, h = b[1] - b[0] + 2 * pad, b[3] - b[2] + 2 * pad
            max_w, max_h = max(max_w, w), max(max_h, h)
            layouts.append({"wrapper": wid, "bounds": b, "offset": (pad - b[0], pad - b[2]), "size": (w, h)})
        root = bytearray()
        frame_count = 0
        for i, (job, lay) in enumerate(zip(jobs, layouts)):
            if i:
                root += sp.tag(28, struct.pack("<H", 1))  # RemoveObject2 depth 1
            ox, oy = lay["offset"]
            root += sp.encode_place(sp.Place(depth=1, char=lay["wrapper"], matrix=(1.0, 0.0, 0.0, 1.0, ox, oy)))
            # a "frames": n job stays placed for n root frames, so nested timelines play (validation captures)
            lay["first_frame"] = frame_count
            for _ in range(job.get("frames", 1)):
                root += sp.tag(1, b"")
                frame_count += 1
        self.frame_count = frame_count
        stage = rect_bytes(0, int(max_w), 0, int(max_h))
        header_body = stage + struct.pack("<BBH", 0, int(self.m.fps) or 24, frame_count)
        tags = sp.tag(69, struct.pack("<I", 0)) + sp.tag(9, b"\xff\xff\xff")
        tags += b"".join(self.m.raw_defs) + b"".join(self.extra_defs) + b"".join(wrappers) + bytes(root) + b"\x00\x00"
        body = header_body + tags
        swf = b"FWS" + bytes([max(self.m.version, 10)]) + struct.pack("<I", 8 + len(body)) + body
        return swf, layouts


def rect_bytes(xmin, xmax, ymin, ymax):
    w = sp.BitWriter()
    n = sp._nbits(xmin, xmax, ymin, ymax)
    w.ub(n, 5)
    for v in (xmin, xmax, ymin, ymax):
        w.sb(v, n)
    return w.flush()


def render_samples(movie, cc, job, samples, zoom, work_dir):
    """Export selected absolute timeline frames without rasterizing intervening frames."""
    os.makedirs(work_dir, exist_ok=True)
    samples = sorted(set(samples))
    if not samples or samples[0] < 0 or samples[-1] >= 65535:
        raise ValueError('Invalid sparse timeline samples')
    rs = RenderSwf(movie, cc)
    swf, layouts = rs.build([dict(job, frames=samples[-1] + 1)])
    path = os.path.join(work_dir, 'samples.swf')
    open(path, 'wb').write(swf)
    output = os.path.join(work_dir, 'frames')
    os.makedirs(output, exist_ok=True)
    for stale in glob.glob(os.path.join(output, '**', '*.png'), recursive=True):
        os.remove(stale)
    ffdec('-ignorebackground', '-zoom', '%g' % zoom, '-select', ','.join(str(f + 1) for f in samples),
          '-format', 'frame:png', '-export', 'frame', output, path,
          profile=os.path.join(work_dir, 'jpexs_profile'))
    paths = {int(os.path.basename(p).removesuffix('.png')) - 1: p
             for p in glob.glob(os.path.join(output, '**', '*.png'), recursive=True)}
    if set(paths) != set(samples):
        raise RuntimeError('Sparse reference capture is incomplete: ' + str(sorted(paths)))
    lay, tpp = layouts[0], 20.0 / zoom
    images, origins = {}, {}
    for f in samples:
        image = Image.open(paths[f]).convert('RGBA')
        box = image.getchannel('A').getbbox() or (0, 0, 1, 1)
        images[f] = image.crop(box)
        origins[f] = (box[0] * tpp - lay['offset'][0], box[1] * tpp - lay['offset'][1])
    return dict(images=images, origins=origins, twips_per_px=tpp)


def render_jobs(movie, cc, jobs, zoom, work_dir, name="render", bucket=True):
    """Render jobs at one zoom. Returns [{"image": RGBA Image, "origin": (x, y) twips of pixel (0,0) in job space,
    "twips_per_px": float}] in job order. Jobs sharing a "group" key get one common crop so variants align."""
    os.makedirs(work_dir, exist_ok=True)
    rs = RenderSwf(movie, cc)
    if bucket and len(jobs) > 1 and sum(job.get('frames', 1) for job in jobs) >= 64:
        # A shared SWF stage otherwise makes every tiny color probe or effect
        # frame pay for the largest piece in the entire render pass. Keep all
        # variants of a crop group together, and split only a substantially
        # wasteful pass into a bounded number of similarly sized stages.
        groups = {}
        for index, job in enumerate(jobs):
            bounds = rs.job_bounds(job['items'])
            width = (bounds[1] - bounds[0]) / 20 * zoom + 2 * rs.PAD_PX * zoom + 1
            height = (bounds[3] - bounds[2]) / 20 * zoom + 2 * rs.PAD_PX * zoom + 1
            group = groups.setdefault(job.get('group', index), dict(indices=[], width=0, height=0, frames=0))
            group['indices'].append(index)
            group['width'] = max(group['width'], width)
            group['height'] = max(group['height'], height)
            group['frames'] += job.get('frames', 1)
        buckets = {}
        for group in groups.values():
            key = (math.ceil(math.log2(max(1, group['width']))), math.ceil(math.log2(max(1, group['height']))))
            buckets.setdefault(key, []).append(group)
        full_pixels = max(g['width'] for g in groups.values()) * max(g['height'] for g in groups.values()) * sum(g['frames'] for g in groups.values())
        bucket_pixels = sum(max(g['width'] for g in gs) * max(g['height'] for g in gs) * sum(g['frames'] for g in gs) for gs in buckets.values())
        if 1 < len(buckets) <= 8 and full_pixels > 4 * bucket_pixels:
            results = [None] * len(jobs)
            for number, (_, gs) in enumerate(sorted(buckets.items())):
                indices = sorted(index for group in gs for index in group['indices'])
                rendered = render_jobs(movie, cc, [dict(jobs[index], group=jobs[index].get('group', index)) for index in indices], zoom, work_dir,
                                       name + '_bucket' + str(number), bucket=False)
                for index, result in zip(indices, rendered):
                    results[index] = result
            return results
    swf, layouts = rs.build(jobs)
    swf_path = os.path.join(work_dir, name + ".swf")
    previous = open(swf_path, 'rb').read() if os.path.isfile(swf_path) else None
    open(swf_path, "wb").write(swf)
    out_dir = os.path.join(work_dir, name + "_frames")
    pngs = glob.glob(os.path.join(out_dir, '**', '*.png'), recursive=True)
    reusable = (previous == swf and len(pngs) == rs.frame_count and
                {int(os.path.basename(p).removesuffix('.png')) for p in pngs} == set(range(1, rs.frame_count + 1)))
    if os.path.isdir(out_dir) and not reusable:
        for f in glob.glob(os.path.join(out_dir, "**", "*.png"), recursive=True):
            os.remove(f)
    if not reusable:
        ffdec("-ignorebackground", "-zoom", "%g" % zoom, "-format", "frame:png", "-export", "frame", out_dir, swf_path,
              profile=os.path.join(work_dir, "jpexs_profile"))
    pngs = glob.glob(os.path.join(out_dir, "**", "*.png"), recursive=True)
    pngs.sort(key=lambda p: int(re.sub(r"\D", "", os.path.basename(p)) or 0))
    if len(pngs) != rs.frame_count:
        raise RuntimeError("expected %d frames, JPEXS wrote %d" % (rs.frame_count, len(pngs)))
    # every image of a group (same job, different colour variants or frames) shares one crop box
    groups = {}
    for i, job in enumerate(jobs):
        groups.setdefault(job.get("group", i), []).append(i)
    results = [None] * len(jobs)
    tpp = 20.0 / zoom
    def union(a, b):
        if b is None:
            return a
        return b if a is None else (min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3]))

    for idxs in groups.values():
        # "crop": "each" (flipbooks) crops every frame on its own (the union over the group's colour variants);
        # otherwise one box covers every frame of every image in the group
        each = any(jobs[i].get("crop") == "each" for i in idxs)
        nframes = max(jobs[i].get("frames", 1) for i in idxs)
        boxes = [None] * nframes
        for i in idxs:
            lay = layouts[i]
            win = (0, 0, int(lay["size"][0] / 20 * zoom) + 1, int(lay["size"][1] / 20 * zoom) + 1)
            for k in range(jobs[i].get("frames", 1)):
                with Image.open(pngs[lay['first_frame'] + k]) as image:
                    bb = image.crop(win).getchannel('A').getbbox()
                boxes[k] = union(boxes[k], bb)
        if not each:
            whole = None
            for b in boxes:
                whole = union(whole, b)
            boxes = [whole] * nframes
        boxes = [b or (0, 0, 1, 1) for b in boxes]
        for i in idxs:
            lay = layouts[i]
            ox, oy = lay["offset"]
            n = jobs[i].get("frames", 1)
            imgs = CroppedFrames(pngs[lay['first_frame']:lay['first_frame'] + n], boxes[:n])
            origins = [(boxes[k][0] * tpp - ox, boxes[k][1] * tpp - oy) for k in range(n)]
            results[i] = {"image": imgs[0], "images": imgs, "twips_per_px": tpp, "origin": origins[0],
                          "origins": origins}
    return results
