"""Convert an AQW Flash armor (classes/M/<file>.swf + classes/F/<file>.swf) into the input for the Unity armor
builder: per gender one sprite atlas, CC masks, ItemSlots order and, for every body-part renderer, a layer stack
(bands + animated tracks) with per-frame transforms for the FlashRig shader.

usage: python armor.py <M swf> <F swf> <template dir (armor_template.py output)> <out dir> [--name Name] [--rgb-cc]

How colour custom survives animation in AQW:I (see docs in the session memory "aqwi-armor-contract"):
  in-game the client composites every ItemSlots entry (sprite rect + CC mask) into one runtime texture with the
  player's colours and gives each renderer Sprite.Create(destTex, rect) of its last entry. Each part therefore
  lists its band/element images as extra entries (all composited, all coloured) and its flattened "base" image
  last. The part's material (FlashRig shader) ignores the base pixels and draws the layer stack from destTex,
  with per-frame affine transforms and colour transforms animated by an Animator on the slot GameObject.
  The base image provides the quad (its alpha covers every frame's footprint) and a fallback look. The native
  character creator swaps in a static CC material; InfinityLoader's FlashRigPreviewColors compatibility hook
  instead composites the complete atlas and retains the animated material in preview screens.

With --rgb-cc, all body parts use FlashRig layers. Seven fixed coefficient atlases retain independent RGB
responses sampled from the source at black, grey128, and white. Three small ItemSlots probes recover the
player's body palette from the native compositor; the shader reconstructs each layer before applying its
source transforms. The native face remains the template's behavior; preview animation needs the loader hook.
"""
import argparse
import hashlib
import json
import math
import os

import numpy as np
from PIL import Image

import ccscripts
import render
import rig
import swfparse as sp
from render import IDENT, mat_inv, mat_mul

# Flash linkage suffix -> front slot, back slot (same symbol drawn as a black silhouette), px per Flash px
PARTS = [("Chest", "chest", None, 1.645), ("Hip", "hip", None, 1.645),
         ("Shoulder", "frontshoulder", "backshoulder", 1.645), ("Hand", "fronthand", "backhand", 1.61),
         ("Thigh", "frontthigh", "backthigh", 1.64), ("Shin", "frontshin", "backshin", 1.665),
         ("Foot", "frontfoot", "backfoot", 1.37), ("FootIdle", "idlefoot", None, 1.265),
         ("Robe", "robe", None, 6.10), ("RobeBack", "backrobe", None, 6.12)]
# native back limbs are the front art scaled about the pivot (measured on Cryo, Warrior and Fool natives)
BACK_SCALE = {"backshoulder": (0.903, 0.903), "backhand": (0.907, 0.907), "backthigh": (1.06, 1.25),
              "backshin": (1.0035, 1.0035), "backfoot": (1.068, 1.068)}
SLOT_ID = {"backhair": 1, "backweapon": 3, "backshoulder": 4, "backhand": 5, "backrobe": 6, "backfoot": 7,
           "backthigh": 8, "backshin": 9, "chest": 11, "hip": 13, "frontthigh": 15, "frontshin": 17,
           "idlefoot": 19, "frontfoot": 21, "robe": 22, "head": 23, "fronthair": 27, "frontshoulder": 30,
           "fronthand": 32, "weapon": 33, "eyes": 34, "eyescolor": 35, "eyeslight": 36, "helm": 37, "eyesblink": 42}
# mask channel and side per colour location (shader: lo = R/G/B "black", hi = "white")
CHANNEL = {"Base": (0, -1), "Trim": (1, -1), "Accessory": (2, -1), "Skin": (0, 1), "Hair": (1, 1), "Eye": (2, 1)}
PALETTE = {"Base": (178, 34, 52), "Trim": (230, 190, 60), "Accessory": (40, 120, 230), "Skin": (240, 200, 160),
           "Hair": (90, 60, 30), "Eye": (30, 160, 60)}
MAX_LAYERS = 32
MORPH_HEAD = ("Head", "head", None, 1.645)
FLIPBOOK_BUDGET_PX = 1_500_000  # per distinct flipbook render (all its frames); above it, render smaller
FLIPBOOK_MAX_DOWNSAMPLE = 2  # thin lines (rings, runes) fade below half resolution
COVER_ALPHA = 3  # /255: invisible alpha painted over every pixel any frame can reach, so the sprite mesh covers it
PAD = 4
RGB_LOCATIONS = ("Base", "Trim", "Accessory")
PROBE_DIFFUSE = 240


def a3(m):
    """Flash matrix tuple -> 3x3 numpy."""
    a, b, c, d, tx, ty = m
    return np.array([[a, c, tx], [b, d, ty], [0, 0, 1.0]])


def scale3(sx, sy=None):
    return np.diag([sx, sx if sy is None else sy, 1.0])


def trans3(tx, ty):
    return np.array([[1, 0, tx], [0, 1, ty], [0, 0, 1.0]])


def max_scale(m):
    a, b, c, d = m[:4]
    s = np.linalg.svd(np.array([[a, c], [b, d]]), compute_uv=False)
    return float(s[0])


# ---------------------------------------------------------------- colour-custom encoding
GREY = 128


def _ls_coefficients(xg=GREY / 255.0, n=41):
    """Least-squares line over colour values C in [0, 1] through the piecewise-linear response sampled at
    C = 0, xg, 1 (renders black, grey, white). Returns coefficient rows for slope, intercept, and the slope of
    the fit forced through the C = 0 value; each is dotted with (f0, f1, f2)."""
    xs = np.linspace(0, 1, n)
    b0 = np.where(xs <= xg, 1 - xs / xg, 0)
    b1 = np.where(xs <= xg, xs / xg, (1 - xs) / (1 - xg))
    b2 = np.where(xs <= xg, 0, (xs - xg) / (1 - xg))
    basis = np.stack([b0, b1, b2], 1)
    P = np.linalg.pinv(np.stack([xs, np.ones_like(xs)], 1))
    through0 = (xs @ np.stack([b0 - 1, b1, b2], 1)) / (xs @ xs)
    return P[0] @ basis, P[1] @ basis, through0


LS_SLOPE, LS_ICPT, LS_SLOPE0 = _ls_coefficients()


def rgb_response(black, whites, greys, locations=RGB_LOCATIONS):
    """Independent RGB three-point responses, before native scalar-mask approximation.

    This bounded path supports the three body locations used by ChronoFrostMage. It retains signed
    slopes; neither source RGB shading nor the native compositor's brightness is folded into a scalar
    mask. Interpolation remains approximate between samples and across interacting CC locations.
    """
    B = np.asarray(black, dtype=np.float32) / 255
    q0, alpha = B[..., :3], B[..., 3]
    slopes, high_slopes = {}, {}
    for loc, white in whites.items():
        f2 = np.asarray(white, dtype=np.float32)[..., :3] / 255
        f1 = np.asarray(greys[loc], dtype=np.float32)[..., :3] / 255
        slope = (f1 - q0) / (GREY / 255)
        high = (f2 - f1) / (1 - GREY / 255)
        if loc not in locations:
            if float(np.abs(slope[alpha > 0]).max(initial=0)) > 0.01:
                raise ValueError("RGB body reconstruction does not support location " + loc)
            continue
        slopes[loc] = np.where((alpha > 0)[..., None], slope, 0).astype(np.float32)
        high_slopes[loc] = np.where((alpha > 0)[..., None], high, 0).astype(np.float32)
    q = np.where((alpha > 0)[..., None], q0, 0).astype(np.float32)
    return {"constant": q, "slopes": slopes, "high_slopes": high_slopes,
            "alpha": np.asarray(black, dtype=np.uint8)[..., 3]}


def cc_encode(black, whites, greys=None):
    """black/whites/greys: RGBA renders with every CC location black / one location white / one location grey.
    Flash's response to a location's colour C is not linear (overlay highlights above the colour clip), while
    AQW:I can only do d*C + constant, so each pixel gets the least-squares line over C in [0, 1].
    Returns (diffuse RGBA uint8, mask RGB uint8 or None)."""
    B = np.asarray(black, dtype=np.float32) / 255
    alpha = B[..., 3]
    q0 = B[..., :3]
    q = q0.copy()
    resp = {}
    for loc, img in whites.items():
        f2 = np.asarray(img, dtype=np.float32)[..., :3] / 255
        if greys and loc in greys:
            f1 = np.asarray(greys[loc], dtype=np.float32)[..., :3] / 255
            slope = LS_SLOPE[0] * q0 + LS_SLOPE[1] * f1 + LS_SLOPE[2] * f2
            icpt = LS_ICPT[0] * q0 + LS_ICPT[1] * f1 + LS_ICPT[2] * f2
            low = icpt < q0  # a darkening curve cannot go below the C=0 value: refit through it
            slope = np.where(low, LS_SLOPE0[0] * q0 + LS_SLOPE0[1] * f1 + LS_SLOPE0[2] * f2, slope)
            q = q + np.where(low, 0, icpt - q0) * (alpha > 0)[..., None]
        else:
            slope = f2 - q0
        resp[loc] = np.clip(slope, 0, 1).mean(axis=2) * (alpha > 0)
    q = np.clip(q, 0, 1)
    if not resp or max(float(r.max()) for r in resp.values()) < 0.02:
        return np.asarray(black, dtype=np.uint8), None
    # one location per mask channel and pixel: keep the stronger of lo/hi when both exist
    w = {}
    for ch in range(3):
        locs = [l for l in resp if CHANNEL[l][0] == ch]
        if not locs:
            continue
        stack = np.stack([resp[l] for l in locs])
        win = stack.argmax(axis=0)
        for i, l in enumerate(locs):
            w[l] = np.where(win == i, resp[l], 0)
    S = sum(w.values())
    colored = S > 0.02
    # shader: out = d*W*C + (1-W)*diffuse.rgb with diffuse.r = d. So d*W = S and (1-W)*d = q.r give
    # d = S + q.r, W = S/d; green/blue of the constant part come from diffuse.gb = q.gb / (1-W).
    d = np.clip(S + q[..., 0], 0, 1)
    Wt = np.clip(S / np.maximum(d, 1e-6), 0.05, 0.99)
    d = np.where(Wt >= 0.99, np.clip(S / 0.99, 0, 1), d)
    D = np.clip(q / np.maximum(1 - Wt, 1e-3)[..., None], 0, 1)
    diffuse = np.where(colored[..., None], np.stack([d, np.where(Wt >= 0.99, d, D[..., 1]),
                                                      np.where(Wt >= 0.99, d, D[..., 2])], axis=2), q0)
    mask = np.full(q.shape, 0.5, dtype=np.float32)
    for l, wl in w.items():
        ch, side = CHANNEL[l]
        share = np.where(colored, Wt * wl / np.maximum(S, 1e-6), 0)
        mask[..., ch] = np.where(share > 0.004, 0.5 + side * (share + 0.01) / 2, mask[..., ch])
    out = np.concatenate([diffuse, alpha[..., None]], axis=2)
    out[alpha <= 0] = 0
    mask[alpha <= 0] = 0.5
    if "Skin" in whites and greys and "Skin" in greys:
        # Native skin is a gradient lookup, not diffuse.r * playerSkin. Match
        # the native face's normal (165) and shadow (90) gradient coordinates.
        f2 = np.asarray(whites["Skin"], np.float32)[..., :3] / 255
        f1 = np.asarray(greys["Skin"], np.float32)[..., :3] / 255
        span = np.maximum((f2 - q0).mean(2), 0)
        ratio = np.clip((f1 - q0).mean(2) / np.maximum(span, 1e-6), 0, 1)
        dark = np.clip((GREY / 255 - ratio) / np.maximum(1 - ratio, 1e-6), 0, 1)
        light = np.clip(1 - (GREY / 255) / np.maximum(ratio, 1e-6), 0, 1)
        offset = light - dark
        coverage = np.clip(span / np.maximum(1 - np.abs(offset), 1e-6), 0, 1)
        skin = (span > 0.02) & (alpha > 0)
        shade = np.clip(165 + np.minimum(offset, 0) * (75 / (125 / 765))
                        + np.maximum(offset, 0) * (90 / (100 / 255)), 0, 255) / 255
        # Keep fixed ink in mixed edge pixels; the mask controls skin coverage.
        ink = np.clip(q0 - coverage[..., None] * light[..., None], 0, 1)
        skin_diffuse = ink / np.maximum(1 - coverage, 1e-6)[..., None]
        skin_diffuse[..., 0] = shade
        skin_diffuse = np.where((coverage > .98)[..., None], shade[..., None], skin_diffuse)
        diffuse = np.where(skin[..., None], skin_diffuse, diffuse)
        skin_mask = np.full_like(mask, .5)
        skin_mask[..., 0] = .5 + (coverage + .01) / 2
        mask = np.where(skin[..., None], skin_mask, mask)
        out = np.concatenate([diffuse, alpha[..., None]], axis=2)
        out[alpha <= 0] = 0
    return (np.clip(out, 0, 1) * 255 + 0.5).astype(np.uint8), (np.clip(mask, 0, 1) * 255 + 0.5).astype(np.uint8)


# ---------------------------------------------------------------- per-gender conversion
class GenderBuild:
    """Renders and encodes a set of pieces from one SWF. Armors: one piece per body part, colour-custom encoded.
    Weapons/pets (colour=False): pieces given explicitly, rendered in their authored colours."""

    def __init__(self, swf_path, gender, work, log, rgb_cc=False, pieces=None, colour=True,
                 expanded=False, color_map=None, palette=None, max_layers=MAX_LAYERS,
                 faithful_effects=False, rgb_locations=RGB_LOCATIONS, interactive_morph=False,
                 source_flipbook=False):
        self.g = gender
        self.swf = swf_path
        self.m = sp.load(swf_path)
        self.colour = colour
        self.cc = ccscripts.cc_chars(self.m, swf_path, os.path.join(work, gender + "_cache")) if colour else {}
        self.timeline_programs = []
        self.interaction = None
        if source_flipbook and (interactive_morph or not faithful_effects):
            raise ValueError('Whole-source flipbooks require faithful noninteractive timelines')
        self.source_flipbook = source_flipbook
        if interactive_morph:
            from interactive_morph import Compiler
            self.interaction = Compiler(self.m, os.path.join(work, gender + '_cache'))
        elif faithful_effects:
            from pathlib import Path
            from timeline_scripts import lower_initial_stops
            cache = Path(work) / (gender + '_cache')
            ccscripts.cc_classes(swf_path, str(cache))
            self.timeline_programs = lower_initial_stops(self.m, cache)
        if color_map:
            self.cc = {cid: (color_map.get(loc, loc), '__SkinDark' if loc == 'Skin' and shade == 'Dark'
                            and color_map.get(loc, loc) != 'Skin' else shade)
                       for cid, (loc, shade) in self.cc.items()}
        self.locs = sorted({loc for loc, _ in self.cc.values() if loc in CHANNEL})
        self.work = work
        self.log = log
        self.rig = rig.Rig(self.m, expanded=expanded, cc=self.cc, faithful_effects=faithful_effects)
        self.geo = render.Geometry(self.m)
        self.rgb_cc = rgb_cc
        self.rgb_locations = rgb_locations
        self.pieces = pieces  # [(suffix, front, back, k, cid)] or None for the armor PARTS table
        self.palette = palette or PALETTE
        self.max_layers = max_layers

    def symbol(self, suffix):
        want = None
        for cid, name in self.m.symbols.items():
            if "." in name:
                continue
            if name.endswith(self.g.upper() + suffix):
                want = cid
        return want

    def variants(self):
        if not self.colour:
            return [("authored", None)]
        black = {l: (0, 0, 0) for l in self.locs}
        out = [("black", black)]
        for l in self.locs:
            v = dict(black)
            v[l] = (255, 255, 255)
            out.append(("white_" + l, v))
            v = dict(black)
            v[l] = (GREY, GREY, GREY)
            out.append(("grey_" + l, v))
        out.append(("palette", {l: self.palette[l] for l in self.locs}))
        return out

    def run(self):
        parts = []
        jobs_by_zoom = {}
        color_locations = {}

        def locations_in(cid, stack=()):
            if cid in color_locations:
                return color_locations[cid]
            found = {self.cc[cid][0]} if cid in self.cc else set()
            ch = self.m.chars.get(cid)
            if ch is not None and ch.kind == 'sprite' and cid not in stack:
                for frame in ch.frames:
                    for place in frame.places:
                        if place.char is not None:
                            found.update(locations_in(place.char, stack + (cid,)))
            color_locations[cid] = found
            return found

        queued = set()

        def add_job(zoom, key, items, frames=1):
            if (zoom, key) in queued:
                return
            queued.add((zoom, key))
            local_colors = set().union(*(locations_in(item['char']) for item in items))
            for vname, var in self.variants():
                # A subtree which never reads a location cannot respond to it.
                # Reuse its black render for those probes rather than asking
                # JPEXS to rasterize identical effect flipbooks repeatedly.
                if self.colour and vname not in ('black', 'palette') and vname.split('_', 1)[1] not in local_colors:
                    continue
                if self.colour and vname == 'palette' and not local_colors:
                    continue
                job = {"items": items, "variant": var, "vkey": vname, "group": key, "key": key, "vname": vname}
                if frames > 1:
                    job.update(frames=frames, crop="each")
                jobs_by_zoom.setdefault(zoom, []).append(job)

        pieces = self.pieces or [(s, f, b, k, self.symbol(s)) for s, f, b, k in PARTS]
        for suffix, front, back, k, cid in pieces:
            if cid is None:
                continue
            layers, morph = (([], None) if self.source_flipbook else
                            (self.interaction.decompose(self.rig, cid, self.max_layers) if self.interaction
                             else (self.rig.decompose(cid), None)))
            if self.source_flipbook or len(layers) > self.max_layers:
                if not self.rig.faithful_effects or morph:
                    raise ValueError('%s %s: %d layers exceed supported limit %d' %
                                     (self.g, suffix, len(layers), self.max_layers))
                period = self.rig.period(cid)
                if self.rig.notes:
                    raise ValueError('Dense effects require an uncapped source clock: ' + repr(self.rig.notes))
                self.log('%s %s: whole source baked over exact period %d (original layers %d)' % (self.g, suffix, period, len(layers)))
                layers = [rig.Layer(kind='flipbook', sprite=cid, period=period,
                    items=[rig.Item(cid, IDENT)], frames=[dict(m=IDENT, cx=None, vis=True) for _ in range(period)])]
            static = len(layers) == 1 and layers[0].kind == "band"
            if static and not self.rgb_cc:
                layers = []  # the base image is the whole part; no layer stack, default sprite material
            part = {"suffix": suffix, "front": front, "back": back, "k": k, "cid": cid, "layers": layers,
                    "images": {}, "static": static and not self.rgb_cc}
            if morph:
                part['morph'] = dict(morph, hit_frames=[[[v * k / 2000 * (1 if i % 2 == 0 else -1)
                    for i, v in enumerate(poly)] for poly in polygons] for polygons in morph['hit_frames']])
            add_job(k, (suffix, "base"), [{"char": cid, "matrix": IDENT}])
            track_scale, fb_scale = {}, {}
            fb_look = lambda L: (L.items[0].char, L.items[0].filters_raw, L.items[0].cxform)  # noqa: E731
            for L in layers:
                if L.kind == "band":
                    continue
                vis = [max_scale(f["m"]) for f in L.frames if f["vis"]] or [1.0]
                if L.kind == "track":
                    track_scale[L.items[0].char] = max(track_scale.get(L.items[0].char, 0), max(vis))
                else:
                    fb_scale[fb_look(L)] = max(fb_scale.get(fb_look(L), 0), max(vis))
            for li, L in enumerate(layers):
                if L.kind == "band":
                    add_job(k, (suffix, "band", li), band_items(L.items))
                elif L.kind == "track":
                    s = track_scale[L.items[0].char]
                    key = (suffix, "el", L.items[0].char)
                    L.render_key, L.render_scale = key, s
                    it = L.items[0]
                    add_job(k, key, [{"char": it.char, "matrix": (s, 0.0, 0.0, s, 0.0, 0.0),
                                      "filters_raw": it.filters_raw}])
                else:
                    # flipbook: render the animated instance itself (own colour + filters) for a whole loop;
                    # instances of one symbol with the same look share the render
                    it = L.items[0]
                    s = fb_scale[fb_look(L)]
                    key = (suffix, "fb", it.char, hash((it.filters_raw, it.cxform)) & 0xFFFF)
                    L.render_key, L.render_scale = key, s
                    add_job(k, key, [{"char": it.char, "matrix": (s, 0.0, 0.0, s, 0.0, 0.0),
                                      "filters_raw": it.filters_raw, "cxform": it.cxform}],
                            frames=L.render_frames or L.period)
            parts.append(part)
        rendered = {}
        for zoom, jobs in jobs_by_zoom.items():
            res = render.render_jobs(self.m, self.cc, jobs, zoom, os.path.join(self.work, self.g + "_render"),
                                     "z%g" % zoom)
            for job, r in zip(jobs, res):
                rendered.setdefault(job["key"], {})[job["vname"]] = r
        self.rendered = rendered
        if self.colour:
            for variants in rendered.values():
                for vname, _ in self.variants():
                    variants.setdefault(vname, variants['black'])
        for part in parts:
            self.encode_part(part)
        return parts

    def encode(self, key, frame=0):
        vs = self.rendered[key]
        if not self.colour:
            r = vs["authored"]
            img = r["images"][frame]
            return {"diffuse": np.asarray(img, dtype=np.uint8), "mask": None, "origin": r["origins"][frame],
                    "tpp": r["twips_per_px"], "key": key}
        pick = lambda v: vs[v]["images"][frame]  # noqa: E731
        diffuse, mask = cc_encode(pick("black"),
                                  {l: pick("white_" + l) for l in self.locs if "white_" + l in vs},
                                  {l: pick("grey_" + l) for l in self.locs if "grey_" + l in vs})
        r = vs["black"]
        out = {"diffuse": diffuse, "mask": mask, "origin": r["origins"][frame], "tpp": r["twips_per_px"],
               "key": key}
        if self.rgb_cc:
            out["response"] = rgb_response(pick("black"), {l: pick("white_" + l) for l in self.locs},
                                            {l: pick("grey_" + l) for l in self.locs}, self.rgb_locations)
        return out

    def encode_part(self, part):
        suffix = part["suffix"]
        base = self.encode((suffix, "base"))
        images = {}
        for li, L in enumerate(part["layers"]):
            if L.kind == "flipbook":
                # one encoded image per distinct frame; identical frames (holds, repeats) share an image
                seen, L.image_keys, L.frame_image = {}, [], []
                cache, unique_sources, image_shapes = {}, {}, {}
                for f in range(L.period):
                    src = L.frame_src[f] if L.frame_src else f
                    if src not in cache:
                        enc = self.encode(L.render_key, src)
                        digest = hashlib.sha256(enc['diffuse'].tobytes())
                        if enc['mask'] is not None:
                            digest.update(enc['mask'].tobytes())
                        response = enc.get('response')
                        if response:
                            digest.update(response['constant'].tobytes())
                            digest.update(response['alpha'].tobytes())
                            for kind in ('slopes', 'high_slopes'):
                                for loc, array in sorted(response[kind].items()):
                                    digest.update(loc.encode()); digest.update(array.tobytes())
                        cache[src] = (enc['diffuse'].shape, digest.digest(),
                                      round(enc['origin'][0], 2), round(enc['origin'][1], 2))
                    sig = cache[src]
                    if sig not in seen:
                        ukey = L.render_key + (len(L.image_keys),)
                        seen[sig] = len(L.image_keys)
                        L.image_keys.append(ukey)
                        unique_sources[ukey] = src
                        image_shapes[ukey] = sig[0]
                    L.frame_image.append(seen[sig])
                L.image_key = L.image_keys[0]
                total = sum(image_shapes[k_][0] * image_shapes[k_][1] for k_ in L.image_keys)
                factor = 1
                while (not self.source_flipbook and total / (factor * factor) > FLIPBOOK_BUDGET_PX
                       and factor < FLIPBOOK_MAX_DOWNSAMPLE):
                    factor += 1
                if factor > 1:
                    self.log("%s %s: flipbook #%d %d images %.1fM px, stored at 1/%d" % (
                        self.g, suffix, L.items[0].char, len(L.image_keys), total / 1e6, factor))
                # Decode each unique frame again only after its storage scale is
                # known. Retaining all unscaled RGBA/response arrays first made
                # a 17k-frame source exhaust desktop memory before packing.
                for k_ in L.image_keys:
                    if k_ not in images:
                        images[k_] = self.encode(L.render_key, unique_sources[k_])
                    if factor > 1 and not images[k_].get('downsample'):
                        downsample_image(images[k_], factor)
                continue
            key = (suffix, "band", li) if L.kind == "band" else L.render_key
            if key not in images:
                images[key] = self.encode(key)
                if L.kind == "band" and any(it.glow for it in L.items):
                    downsample_image(images[key], 2)  # a soft knockout glow loses nothing at half resolution
            L.image_key = key
            L.image_keys, L.frame_image = [key], None
        # canvas = frame-0 base plus every place an animated track reaches over its loop
        tb = base["tpp"]
        bb = (base["origin"][0], base["origin"][0] + base["diffuse"].shape[1] * tb,
              base["origin"][1], base["origin"][1] + base["diffuse"].shape[0] * tb)
        for L in part["layers"]:
            if L.kind == "band" and not hasattr(L, 'morph_frame'):
                continue
            if L.kind == 'band':
                L.frames = [{'m': IDENT, 'cx': None, 'vis': True}]
                L.render_scale = 1
            for fi, f in enumerate(L.frames):
                if not f["vis"]:
                    continue
                im = images[layer_image_key(L, fi)]
                h, w = im["diffuse"].shape[:2]
                ex0, ey0 = im["origin"]
                corners = [(ex0, ey0), (ex0 + w * im["tpp"], ey0), (ex0, ey0 + h * im["tpp"]),
                           (ex0 + w * im["tpp"], ey0 + h * im["tpp"])]
                M = a3(f["m"]) @ scale3(1 / L.render_scale)
                for x, y in corners:
                    px, py, _ = M @ np.array([x, y, 1.0])
                    bb = (min(bb[0], px), max(bb[1], px), min(bb[2], py), max(bb[3], py))
        x0 = math.floor((bb[0] - base["origin"][0]) / tb) - 2
        y0 = math.floor((bb[2] - base["origin"][1]) / tb) - 2
        x1 = math.ceil((bb[1] - base["origin"][0]) / tb) + 2
        y1 = math.ceil((bb[3] - base["origin"][1]) / tb) + 2
        W, H = x1 - x0, y1 - y0
        canvas = np.zeros((H, W, 4), np.uint8)
        bh, bw = base["diffuse"].shape[:2]
        canvas[-y0:-y0 + bh, -x0:-x0 + bw] = base["diffuse"]
        cmask = None
        if base["mask"] is not None:
            cmask = np.full((H, W, 3), 128, np.uint8)
            cmask[-y0:-y0 + bh, -x0:-x0 + bw] = base["mask"]
        origin = (base["origin"][0] + x0 * tb, base["origin"][1] + y0 * tb)
        cover = canvas[..., 3] > 0
        to_part = trans3(origin[0], origin[1]) @ scale3(tb)  # canvas px -> part twips
        # every (image, pose) an animated layer can show; flipbook poses are static, so each image counts once
        poses = {}
        for L in part["layers"]:
            if L.kind == "band" and not hasattr(L, 'morph_frame'):
                continue
            step = 1 if L.kind == "flipbook" else max(1, len(L.frames) // 48)
            for fi in range(0, len(L.frames), step):
                f = L.frames[fi]
                if f["vis"]:
                    poses[(layer_image_key(L, fi), tuple(round(v, 4) for v in f["m"]), L.render_scale)] = (L, f)
        for (ikey, _, rscale), (L, f) in poses.items():
            im = images[ikey]
            ea = im["diffuse"][..., 3] > 8
            eh, ew = ea.shape
            to_img = (scale3(1 / im["tpp"]) @ trans3(-im["origin"][0], -im["origin"][1]) @ scale3(rscale)
                      @ np.linalg.inv(a3(f["m"])) @ to_part)  # canvas px -> element image px
            # only test canvas pixels inside the element's transformed bounding box
            back = np.linalg.inv(to_img)
            cx, cy = [], []
            for u, v in ((0, 0), (ew, 0), (0, eh), (ew, eh)):
                px, py, _ = back @ np.array([u, v, 1.0])
                cx.append(px); cy.append(py)
            x0c, x1c = max(0, int(math.floor(min(cx)))), min(W, int(math.ceil(max(cx))) + 1)
            y0c, y1c = max(0, int(math.floor(min(cy)))), min(H, int(math.ceil(max(cy))) + 1)
            if x0c >= x1c or y0c >= y1c:
                continue
            ys, xs = np.mgrid[y0c:y1c, x0c:x1c]
            u = to_img[0, 0] * (xs + 0.5) + to_img[0, 1] * (ys + 0.5) + to_img[0, 2]
            v = to_img[1, 0] * (xs + 0.5) + to_img[1, 1] * (ys + 0.5) + to_img[1, 2]
            ui, vi = np.floor(u).astype(int), np.floor(v).astype(int)
            ok = (ui >= 0) & (vi >= 0) & (ui < ew) & (vi < eh)
            hit = np.zeros(ui.shape, bool)
            hit[ok] = ea[vi[ok], ui[ok]]
            cover[y0c:y1c, x0c:x1c] |= hit
        # dilate the footprint by 2 px so bilinear edges stay inside the mesh
        for _ in range(2):
            c = cover.copy()
            c[1:] |= cover[:-1]; c[:-1] |= cover[1:]; c[:, 1:] |= cover[:, :-1]; c[:, :-1] |= cover[:, 1:]
            cover = c
        canvas[..., 3] = np.where(cover & (canvas[..., 3] < COVER_ALPHA), COVER_ALPHA, canvas[..., 3])
        part["base"] = {"diffuse": canvas, "mask": cmask, "origin": origin, "tpp": tb}
        part["images"] = images
        if part["back"]:
            sx, sy = BACK_SCALE[part["back"]]
            part["back_base"] = back_silhouette(canvas, origin, tb, sx, sy)


def downsample_image(im, factor):
    """Store an encoded image at 1/factor resolution (box filter, padded to a multiple of the factor so the
    twips-per-pixel scale stays exact); its mask is reduced the same way."""
    d = im["diffuse"]
    h, w = d.shape[:2]
    H, W = -(-h // factor) * factor, -(-w // factor) * factor
    pad = np.zeros((H, W, 4), np.float32)
    pad[:h, :w] = d
    # premultiplied box filter, so transparent pixels do not darken edges
    pad[..., :3] *= pad[..., 3:4] / 255.0
    small = pad.reshape(H // factor, factor, W // factor, factor, 4).mean(axis=(1, 3))
    rgb = np.where(small[..., 3:4] > 0, small[..., :3] / np.maximum(small[..., 3:4] / 255.0, 1e-6), 0)
    im["diffuse"] = np.clip(np.concatenate([rgb, small[..., 3:4]], axis=2) + 0.5, 0, 255).astype(np.uint8)
    if im.get("mask") is not None:
        m = np.full((H, W, 3), 128, np.float32)
        m[:h, :w] = im["mask"]
        im["mask"] = np.clip(m.reshape(H // factor, factor, W // factor, factor, 3).mean(axis=(1, 3)) + 0.5,
                             0, 255).astype(np.uint8)
    if im.get('response') is not None:
        response = im['response']
        alpha = np.zeros((H, W), np.float32)
        alpha[:h, :w] = response['alpha'] / 255.0
        reduced_alpha = alpha.reshape(H // factor, factor, W // factor, factor).mean(axis=(1, 3))
        def reduce_coefficient(array):
            padded = np.zeros((H, W, 3), np.float32)
            padded[:h, :w] = array
            mean = (padded * alpha[..., None]).reshape(H // factor, factor, W // factor, factor, 3).mean(axis=(1, 3))
            return mean / np.maximum(reduced_alpha[..., None], 1e-8)
        response['constant'] = reduce_coefficient(response['constant'])
        for kind in ('slopes', 'high_slopes'):
            response[kind] = {loc: reduce_coefficient(array) for loc, array in response[kind].items()}
        response['alpha'] = np.clip(reduced_alpha * 255 + 0.5, 0, 255).astype(np.uint8)
    im["tpp"] = im["tpp"] * factor
    im["downsample"] = im.get("downsample", 1) * factor


def layer_image_key(L, frame):
    """The encoded image a layer shows on a frame (flipbooks switch images, other layers keep one)."""
    if L.frame_image is None:
        return L.image_keys[0]
    return L.image_keys[L.frame_image[frame % len(L.frame_image)]]


def band_items(items):
    """Rig items -> render items, remapping mask clip depths to the band's own depth numbering."""
    out = []
    for i, it in enumerate(items):
        d = {"char": it.char, "matrix": it.matrix, "cxform": it.cxform, "blend": it.blend,
             "filters_raw": it.filters_raw}
        if it.clip:
            last = i
            for j in range(i + 1, len(items)):
                if it.depth < items[j].depth <= it.clip:
                    last = j
                else:
                    break
            d["clip"] = last + 1
        out.append(d)
    return out


def back_silhouette(canvas, origin, tb, sx, sy):
    """Black copy of the front base scaled about the pivot (part origin), as the client's back limbs are."""
    H, W = canvas.shape[:2]
    corners = np.array([[origin[0], origin[0] + W * tb], [origin[1], origin[1] + H * tb]])
    bx0, bx1 = sorted(corners[0] * sx)
    by0, by1 = sorted(corners[1] * sy)
    ox = math.floor(bx0 / tb) * tb
    oy = math.floor(by0 / tb) * tb
    BW, BH = int(math.ceil((bx1 - ox) / tb)) + 1, int(math.ceil((by1 - oy) / tb)) + 1
    ys, xs = np.mgrid[0:BH, 0:BW]
    # back canvas px -> back twips -> front twips -> front canvas px
    fx = ((ox + (xs + 0.5) * tb) / sx - origin[0]) / tb
    fy = ((oy + (ys + 0.5) * tb) / sy - origin[1]) / tb
    # bilinear sample of the front alpha
    a = solid_silhouette(canvas)[..., 3].astype(np.float32)
    x0, y0 = np.floor(fx - 0.5).astype(int), np.floor(fy - 0.5).astype(int)
    tx, ty = fx - 0.5 - x0, fy - 0.5 - y0

    def at(yy, xx):
        ok = (xx >= 0) & (yy >= 0) & (xx < W) & (yy < H)
        out = np.zeros(xx.shape, np.float32)
        out[ok] = a[yy[ok], xx[ok]]
        return out

    alpha = (at(y0, x0) * (1 - tx) * (1 - ty) + at(y0, x0 + 1) * tx * (1 - ty) + at(y0 + 1, x0) * (1 - tx) * ty
             + at(y0 + 1, x0 + 1) * tx * ty)
    img = np.zeros((BH, BW, 4), np.uint8)
    img[..., 3] = np.clip(alpha + 0.5, 0, 255).astype(np.uint8)
    return {"diffuse": img, "mask": None, "origin": (ox, oy), "tpp": tb, "scale": (sx, sy)}


def solid_silhouette(rgba):
    """Fill internal vector seams in a black limb; retain the outer antialiased edge."""
    from scipy.ndimage import binary_fill_holes, binary_erosion, binary_closing
    alpha = rgba[..., 3]
    shape = binary_fill_holes(binary_closing(alpha > 8))
    interior = binary_erosion(shape)
    out = np.zeros_like(rgba)
    out[..., 3] = np.where(interior, 255, alpha)
    return out


# ---------------------------------------------------------------- atlas packing
def pack(sizes, width):
    """Skyline bottom-left packer. sizes: [(w, h)] -> positions [(x, y)] top-left, total height."""
    order = sorted(range(len(sizes)), key=lambda i: (-max(sizes[i]), -sizes[i][0] * sizes[i][1]))
    sky = [(0, width, 0)]  # segments (x, w, top)
    pos = [None] * len(sizes)
    for i in order:
        w, h = sizes[i][0] + PAD, sizes[i][1] + PAD
        if w > width:
            return None, None
        best = None
        for si in range(len(sky)):
            x = sky[si][0]
            if x + w > width:
                break
            top, span, j = 0, 0, si
            while span < w:
                top = max(top, sky[j][2])
                span += sky[j][1]
                j += 1
            if best is None or top + h < best[0] or (top + h == best[0] and x < best[1]):
                best = (top + h, x, top)
        _, x, top = best
        pos[i] = (x + PAD // 2, top + PAD // 2)
        # raise the skyline over [x, x + w)
        new, end = [], x + w
        for sx, sw, st in sky:
            if sx + sw <= x or sx >= end:
                new.append((sx, sw, st))
                continue
            if sx < x:
                new.append((sx, x - sx, st))
            if sx + sw > end:
                new.append((end, sx + sw - end, st))
        new.append((x, w, top + h))
        new.sort()
        merged = []
        for seg in new:
            if merged and merged[-1][2] == seg[2] and merged[-1][0] + merged[-1][1] == seg[0]:
                merged[-1] = (merged[-1][0], merged[-1][1] + seg[1], seg[2])
            else:
                merged.append(seg)
        sky = merged
    return pos, max(st for _, _, st in sky)


def pot(n):
    p = 1
    while p < n:
        p *= 2
    return p


def pot_nearest(n):
    lo = 1 << max(0, int(math.floor(math.log2(max(1, n)))))
    return lo if n - lo < lo * 2 - n else lo * 2


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("swf_m")
    ap.add_argument("swf_f")
    ap.add_argument("template")
    ap.add_argument("out")
    ap.add_argument("--name")
    ap.add_argument('--source-head', '--morph', dest='morph', action='store_true',
                    help='Preserve the exported source head and suppress the standard face overlays')
    ap.add_argument('--interactive-morph', action='store_true', help='Compile supported source click-to-play timelines with independent child clocks')
    ap.add_argument('--faithful-effects', action='store_true', help='Render animated masks and filters with their complete subtree')
    ap.add_argument("--max-period", type=int, default=rig.MAX_PERIOD,
                    help="maximum preserved layer loop in frames (1..10000); longer loops are reported")
    ap.add_argument("--rgb-cc", action="store_true",
                    help="preserve three-point RGB body CC responses using fixed coefficient atlases")
    ap.add_argument('--six-colors',action='store_true',help='Retain independent body, skin, hair and eye RGB response channels')
    args = ap.parse_args()
    if args.interactive_morph and not args.rgb_cc:
        ap.error('--interactive-morph requires --rgb-cc for all-state player colors')
    if args.six_colors and not args.rgb_cc:ap.error('--six-colors requires --rgb-cc')
    if not 1 <= args.max_period <= 10000:
        ap.error("--max-period must be between 1 and 10000")
    rig.MAX_PERIOD = args.max_period
    name = args.name or os.path.splitext(os.path.basename(args.swf_m))[0]
    os.makedirs(args.out, exist_ok=True)
    work = os.path.join(args.out, "work")
    template = json.load(open(os.path.join(args.template, "template.json")))
    notes = []
    log = lambda s: (notes.append(s), print(s, flush=True))  # noqa: E731
    result = {"name": name, "fps": 24, "genders": {}, "rgb_cc": args.rgb_cc, "rig_notes":{}, "source":{'faithful_effects':args.faithful_effects,'six_colors':args.six_colors}}
    for g, swf in (("m", args.swf_m), ("f", args.swf_f)):
        locations = tuple(CHANNEL) if args.interactive_morph or args.six_colors else RGB_LOCATIONS
        gb = GenderBuild(swf, g, work, log, rgb_cc=args.rgb_cc, faithful_effects=args.faithful_effects,
                         expanded=args.interactive_morph, max_layers=128 if args.interactive_morph else MAX_LAYERS,
                         rgb_locations=locations, interactive_morph=args.interactive_morph)
        if args.morph or args.interactive_morph:
            head = gb.symbol('Head')
            if head is None:
                raise ValueError('Morph armor has no exported ' + g.upper() + 'Head symbol')
            gb.pieces = [(s, f, b, k, gb.symbol(s)) for s, f, b, k in [*PARTS, MORPH_HEAD]]
        parts = gb.run()
        result['rig_notes'][g]=list(gb.rig.notes)
        notes.extend("%s: %s" % (g, n) for n in gb.rig.notes)
        result["genders"][g] = build_gender_output(name, g, parts, template[g], args.template, args.out, log,
            rgb_cc=args.rgb_cc, source_face=args.morph or args.interactive_morph, reconstruction_locations=locations)
        if args.interactive_morph or args.six_colors:
            result['genders'][g]['color_reconstruction'].update(direct_palette={loc: loc for loc in CHANNEL},
                palette=PALETTE, coefficient_format='BC7', slope_coefficient_format='DXT1')
            result['genders'][g]['atlas_format'] = 'BC7'
    result['morph'] = args.morph or args.interactive_morph
    result['interactive_morph'] = args.interactive_morph
    result["notes"] = notes
    json.dump(result, open(os.path.join(args.out, "armor.json"), "w"), indent=1)
    print("wrote", os.path.join(args.out, "armor.json"))


def build_gender_output(name, g, parts, tmpl, tmpl_dir, out, log, rgb_cc=False, source_face=False,
                        reconstruction_locations=RGB_LOCATIONS):
    """Pack the atlas, write masks, and emit ItemSlots order plus per-renderer layer stacks."""
    images = []  # (id, rgba, mask or None)
    for part in parts:
        for key, im in part["images"].items():
            images.append(("%s_%s" % (part["suffix"], "_".join(str(k).replace(".", "p") for k in key[1:])),
                           im["diffuse"], im["mask"]))
            im["id"] = images[-1][0]
        images.append(("%s_base" % part["suffix"], part["base"]["diffuse"], part["base"]["mask"]))
        part["base"]["id"] = images[-1][0]
        if part.get("back_base"):
            images.append(("%s_backbase" % part["suffix"], part["back_base"]["diffuse"], None))
            part["back_base"]["id"] = images[-1][0]
            part["back_images"] = {}
            for key, im in part["images"].items():
                back = dict(im, diffuse=solid_silhouette(im["diffuse"]), mask=None)
                if "response" in im:
                    response = im["response"]
                    back["response"] = {"constant": np.zeros_like(response["constant"]),
                        "alpha": back["diffuse"][..., 3],
                        "slopes": {}, "high_slopes": {}}
                back["id"] = im["id"] + "_silhouette"
                part["back_images"][key] = back
                images.append((back["id"], back["diffuse"], None))
    if rgb_cc:
        for loc in RGB_LOCATIONS:
            rgba = np.full((8, 8, 4), PROBE_DIFFUSE, np.uint8)
            rgba[..., 3] = 255
            mask = np.full((8, 8, 3), 128, np.uint8)
            mask[..., CHANNEL[loc][0]] = 0
            images.append(("probe_" + loc, rgba, mask))
    face = {} if source_face else tmpl["face"]
    if not source_face and not face.get("head", {}).get("mask_png"):
        raise ValueError("Armor face template has no skin mask; use a native skin-customizable face kit")
    for slot in ("head", "eyes", "eyescolor", "eyeslight", "eyesblink"):
        if slot in face:
            img = np.asarray(Image.open(os.path.join(tmpl_dir, face[slot]["png"])).convert("RGBA"))
            msk = None
            if face[slot].get("mask_png"):
                msk = np.asarray(Image.open(os.path.join(tmpl_dir, face[slot]["mask_png"])).convert("RGB"))
            images.append(("face_" + slot, img, msk))
    sizes = [(im[1].shape[1], im[1].shape[0]) for im in images]
    for width in (1024, 2048, 4096):
        pos, height = pack(sizes, width)
        if pos is not None and height <= width * 2:
            break
    aw, ah = width, pot(height)
    atlas = np.zeros((ah, aw, 4), np.uint8)
    mask_dir = os.path.join(out, g + "_masks")
    os.makedirs(mask_dir, exist_ok=True)
    sprites = {}
    for (iid, rgba, msk), (x, y) in zip(images, pos):
        h, w = rgba.shape[:2]
        atlas[y:y + h, x:x + w] = rgba
        entry = {"name": "%s_%s_%s" % (name, g, iid), "rect": [x, ah - (y + h), w, h], "pil": [x, y, w, h],
                 "pivot": [0.5, 0.5], "ppu": 100, "mask": None}
        if msk is not None:
            mw, mh = pot_nearest(w), pot_nearest(h)
            if (msk == msk.reshape(-1, 3)[0]).all():
                mw = mh = 4  # a uniform mask needs no resolution
            Image.fromarray(msk).resize((mw, mh), Image.BILINEAR).save(os.path.join(mask_dir, iid + "_mask.png"))
            entry["mask"] = "%s_masks/%s_mask.png" % (g, iid)
        sprites[iid] = entry
    Image.fromarray(atlas).save(os.path.join(out, "%s_atlas.png" % g))
    # pivots: base sprites pivot on the Flash registration point (part twips origin)
    for part in parts:
        for which in ("base", "back_base"):
            b = part.get(which)
            if not b:
                continue
            e = sprites[b["id"]]
            w, h = e["rect"][2], e["rect"][3]
            px, py = -b["origin"][0] / b["tpp"], -b["origin"][1] / b["tpp"]
            e["pivot"] = [px / w, 1 - py / h]
    for slot in ("head", "eyes", "eyescolor", "eyeslight", "eyesblink"):
        if "face_" + slot in sprites:
            sprites["face_" + slot]["pivot"] = face[slot]["pivot"]
            sprites["face_" + slot]["ppu"] = face[slot]["ppu"]
    # Probes must be composited before the chest base, which remains the slot's displayed sprite.
    itemslots = []
    if rgb_cc:
        itemslots.extend({"slot": SLOT_ID["chest"], "sprite": sprites["probe_" + loc]["name"]}
                         for loc in RGB_LOCATIONS)
    renderers = {}
    for part in parts:
        for key, im in part["images"].items():
            itemslots.append({"slot": SLOT_ID[part["front"]], "sprite": sprites[im["id"]]["name"]})
        for im in part.get("back_images", {}).values():
            itemslots.append({"slot": SLOT_ID[part["back"]], "sprite": sprites[im["id"]]["name"]})
    for part in parts:
        itemslots.append({"slot": SLOT_ID[part["front"]], "sprite": sprites[part["base"]["id"]]["name"]})
        renderers[part["front"]] = renderer_stack(part, part["base"], sprites, ah, None)
        if part.get("back_base"):
            itemslots.append({"slot": SLOT_ID[part["back"]], "sprite": sprites[part["back_base"]["id"]]["name"]})
            renderers[part["back"]] = renderer_stack(part, part["back_base"], sprites, ah,
                                                     part["back_base"]["scale"])
    for slot in ("head", "eyes", "eyescolor", "eyeslight", "eyesblink"):
        if "face_" + slot in sprites:
            itemslots.append({"slot": SLOT_ID[slot], "sprite": sprites["face_" + slot]["name"]})
            renderers[slot] = {"sprite": sprites["face_" + slot]["name"], "layers": None}
    # slots[0] decides the composite texture: it must live in our atlas (all of them do)
    log("%s: atlas %dx%d, %d sprites, %d masks, %d ItemSlots entries" % (
        g, aw, ah, len(sprites), sum(1 for s in sprites.values() if s["mask"]), len(itemslots)))
    result = {"atlas": "%s_atlas.png" % g, "atlas_size": [aw, ah], "sprites": list(sprites.values()),
              "itemslots": itemslots, "renderers": renderers}
    if source_face:
        from build_armor import flatten_template
        nodes = flatten_template(tmpl['tree'])
        for node in nodes:
            if node['name'].lower() in ('eyes', 'eyescolor', 'eyeslight', 'eyesblink'):
                node['rendererEnabled'] = False
        result['nodes'] = nodes
        result['source_face'] = True
    if rgb_cc:
        result["color_reconstruction"] = write_reconstruction_atlases(g, parts, sprites, aw, ah, out,
            compact=len(reconstruction_locations) > 3, locations=reconstruction_locations)
        result["color_reconstruction"]["probes"] = [
            {"location": loc, "rect": sprites["probe_" + loc]["rect"],
             "gain": PROBE_DIFFUSE / 255 * 0.99, "bias": PROBE_DIFFUSE / 255 * (0.01 + 0.05 * 0.99)}
            for loc in RGB_LOCATIONS]
    return result


def write_reconstruction_atlases(g, parts, sprites, aw, ah, out, compact=False, locations=RGB_LOCATIONS):
    """Fixed RGBA32 coefficient textures aligned with the diffuse atlas, including its transparent gutter.

    Per-channel global affine encoding retains signed values without clipping. Zero-valued gutter RGB is
    encoded using the same descriptor as the image, so bilinear interpolation has the same zero boundary
    as the source alpha. These textures bypass the game's native color compositor.
    """
    entries = [(im, sprites[im["id"]]) for part in parts
               for im in list(part["images"].values()) + list(part.get("back_images", {}).values())]

    def write(kind, location=None):
        arrays = [im["response"]["constant"] if kind == "constant" else
                  im["response"]["slopes" if kind == "slope" else "high_slopes"].get(
                      location, np.zeros_like(im["response"]["constant"]))
                  for im, _ in entries]
        lower = np.minimum(0, np.min([a.min(axis=(0, 1)) for a in arrays], axis=0))
        upper = np.maximum(0, np.max([a.max(axis=(0, 1)) for a in arrays], axis=0))
        scale = np.maximum(upper - lower, 1e-6).astype(np.float32)
        bias = lower.astype(np.float32)
        zero = np.clip(np.rint(-bias / scale * 255), 0, 255).astype(np.uint8)
        atlas = np.zeros((ah, aw, 4), np.uint8)
        atlas[..., :3] = zero
        for (im, sprite), a in zip(entries, arrays):
            x, y, w, h = sprite["pil"]
            atlas[y:y+h, x:x+w, :3] = np.clip(np.rint((a - bias) / scale * 255), 0, 255).astype(np.uint8)
            atlas[y:y+h, x:x+w, 3] = im["response"]["alpha"] if kind == 'constant' or not compact else 255
        if compact and kind != 'constant':
            atlas[..., 3] = 255
            if (atlas[..., :3] == atlas[0, 0, :3]).all():
                atlas = np.broadcast_to(atlas[0, 0], (4, 4, 4)).copy()
        path = "%s_%s.png" % (g, kind if location is None else kind + "_" + location.lower())
        Image.fromarray(atlas).save(os.path.join(out, path))
        return {"path": path, "decode_scale": scale.tolist(), "decode_bias": bias.tolist()}

    return {"model": "piecewise_linear", "knot": GREY / 255, "locations": list(locations),
            "constant_atlas": write("constant"),
            "slope_atlases": {loc: write("slope", loc) for loc in locations},
            "high_slope_atlases": {loc: write("high_slope", loc) for loc in locations},
            "fit": {"method": "per-channel piecewise-linear interpolation of black/128/white renders",
                    "samples": [0, GREY, 255],
                    "limitation": "intermediate Flash shade clipping and cross-location interactions remain approximate"}}


def renderer_stack(part, base, sprites, ah, back_scale):
    """Per-layer shader parameters (None for static parts: they draw their base with the default material). The shader maps the fragment's atlas pixel P (y up) to the layer image's
    pixel Q = A·P + T (y up inside the layer's rect) and samples destTex at rect.xy + Q."""
    e = sprites[base["id"]]
    if part["static"]:
        return {"sprite": e["name"], "layers": None}
    bx, by, bw, bh = e["pil"]
    tb = base["tpp"]
    # atlas px (y up) -> base canvas px (y down) -> part twips (back parts: back twips -> front twips)
    atlas_to_canvas = np.array([[1, 0, -bx], [0, -1, ah - by], [0, 0, 1.0]])
    canvas_to_twips = trans3(base["origin"][0], base["origin"][1]) @ scale3(tb)
    to_front = scale3(1 / back_scale[0], 1 / back_scale[1]) if back_scale else np.eye(3)
    P_to_part = to_front @ canvas_to_twips @ atlas_to_canvas
    layers = []
    for L in part["layers"]:
        keys = []
        frames = L.frames if L.kind != "band" else [{"m": IDENT, "cx": None, "vis": True}]
        for fi, f in enumerate(frames):
            im = part["back_images" if back_scale else "images"][layer_image_key(L, fi)]
            s = sprites[im["id"]]
            lx, ly, lw, lh = s["pil"]
            twips_to_img = scale3(1 / im["tpp"]) @ trans3(-im["origin"][0], -im["origin"][1])
            img_to_rect = np.array([[1, 0, 0], [0, -1, lh], [0, 0, 1.0]])
            if L.kind == "band":
                el = np.eye(3)  # bands are rendered in part twips directly
            else:
                el = scale3(L.render_scale) @ np.linalg.inv(a3(f["m"]))
            M = img_to_rect @ twips_to_img @ el @ P_to_part
            cm, ca = (1, 1, 1, 1), (0, 0, 0, 0)
            if f.get("cx"):
                mult, add = f["cx"]
                cm = tuple(v / 256 for v in mult)
                ca = tuple(v / 255 for v in add)
            if not f["vis"]:
                cm = (cm[0], cm[1], cm[2], 0)
            x, y, w, h = s["rect"]
            # +1: the shader's rect is grown by the 1 px transparent gutter so edges keep their anti-aliasing
            keys.append([M[0, 0], M[0, 1], M[1, 0], M[1, 1], M[0, 2] + 1, M[1, 2] + 1] + list(cm) + list(ca)
                        + [x - 1, y - 1, w + 2, h + 2])
        layers.append({"rect": keys[0][14:18], "period": L.period if L.kind != "band" else 1, "keys": keys,
                       "blend": L.blend or 0, "stepped": L.kind == "flipbook",
                       "group_start": L.group_start, "group_end": L.group_end,
                       "morph_frame": getattr(L, 'morph_frame', -1)})
    result = {"sprite": e["name"], "layers": layers, "tint": [0, 0, 0, 1] if back_scale else [1, 1, 1, 1]}
    if part.get('morph'):
        result['morph'] = dict(part['morph'])
        if back_scale:
            result['morph']['hit_frames'] = [[[v * back_scale[i % 2] for i, v in enumerate(poly)]
                for poly in polygons] for polygons in part['morph']['hit_frames']]
    return result


if __name__ == "__main__":
    main()
