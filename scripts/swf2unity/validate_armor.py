"""Check a converted armor without Unity: emulate AQW:I's in-game colour compositor and the FlashRig layer shader
in numpy, then compare source samples of each body part with JPEXS renders of the original Flash part in the
same colours. By default it checks the longest individual layer period, which is not necessarily the complete
joint cycle; every report entry records both the sampled count and the joint period.

usage: python validate_armor.py <armor out dir> <M swf> <F swf> [--frames N] [--sheet]
"""
import argparse
import json
import math
import os

import numpy as np
from PIL import Image

import ccscripts
import render
import swfparse as sp
from armor import PALETTE, PARTS, MORPH_HEAD

COLOR = {k: np.array(v, np.float32) / 255 for k, v in PALETTE.items()}


def skin_gradient(coordinate, color):
    """Client SkinGradient.SetGradient/CreateGradientTexture (32 point-sampled texels)."""
    base = color + np.array([-.03137, .02352, .01176])
    keys = np.clip(np.array([base-.39215, base+[-.11372,-.19607,-.19607], base, base+.39215]), 0, 1)
    values = np.stack([np.interp(np.linspace(0,1,32), [0,.11,.39,1], keys[:,ch]) for ch in range(3)], 1)
    values = np.where(values <= .04045, values / 12.92, ((values+.055)/1.055)**2.4)
    values = np.rint(values*255)/255
    return values[np.clip((coordinate*32).astype(int),0,31)]


def composite(atlas, out_dir, gdata, colors=None):
    """destTex as the client builds it: every ItemSlots entry rect, CC-coloured when it has a mask."""
    A = atlas.astype(np.float32) / 255
    colors = COLOR if colors is None else colors
    H = A.shape[0]
    dest = np.zeros_like(A)
    by_name = {s["name"]: s for s in gdata["sprites"]}
    for e in gdata["itemslots"]:
        s = by_name[e["sprite"]]
        x, y, w, h = s["rect"]
        y0 = H - (y + h)
        d = A[y0:y0 + h, x:x + w]
        if s["mask"]:
            m = np.asarray(Image.open(os.path.join(out_dir, s["mask"])).convert("RGB").resize((w, h), Image.BILINEAR),
                           np.float32) / 255
            hi = np.clip(2 * (m - 0.5) - 0.01, 0, 1)
            lo = np.clip(-2 * (m - 0.5) - 0.01, 0, 1)
            W = hi.sum(2) + lo.sum(2)
            unm = np.clip(d[..., :3] * (1 - W)[..., None], 0, 1)
            bright = np.clip(W * (1 - hi[..., 0]) * d[..., 0] * 0.05, 0, 1)
            unm = unm + bright[..., None]
            col = np.clip(hi[..., 2:3] * colors["Eye"]
                          + hi[..., 1:2] * colors["Hair"] + lo[..., 0:1] * colors["Base"]
                          + lo[..., 1:2] * colors["Trim"] + lo[..., 2:3] * colors["Accessory"], 0, 1)
            out = d[..., 0:1] * col + unm + hi[...,0:1] * skin_gradient(d[...,0], colors["Skin"])
            dest[y0:y0 + h, x:x + w, :3] = out
            dest[y0:y0 + h, x:x + w, 3] = d[..., 3]
        else:
            dest[y0:y0 + h, x:x + w] = d
    # The client's ARGB32 render texture quantizes the result of each native compositor blit.
    return np.rint(np.clip(dest, 0, 1) * 255) / 255


def bilinear(tex, u, v):
    """tex (H,W,C) y-down array addressed with y-up texel coords (u, v), clamp."""
    H, W = tex.shape[:2]
    x = u - 0.5
    y = (H - v) - 0.5
    x0 = np.floor(x).astype(int)
    y0 = np.floor(y).astype(int)
    tx = (x - x0)[..., None]
    ty = (y - y0)[..., None]
    x0c, x1c = np.clip(x0, 0, W - 1), np.clip(x0 + 1, 0, W - 1)
    y0c, y1c = np.clip(y0, 0, H - 1), np.clip(y0 + 1, 0, H - 1)
    return (tex[y0c, x0c] * (1 - tx) * (1 - ty) + tex[y0c, x1c] * tx * (1 - ty) + tex[y1c, x0c] * (1 - tx) * ty
            + tex[y1c, x1c] * tx * ty)


def load_reconstruction(out_dir, gd):
    """Load fixed coefficient textures; their palette is decoded later from the composited probe pixels."""
    spec = gd.get("color_reconstruction")
    if spec is None:
        return None
    expected = tuple(reversed(gd["atlas_size"]))

    def load(desc):
        tex = np.asarray(Image.open(os.path.join(out_dir, desc["path"])).convert("RGBA"), np.float32) / 255
        if tex.shape[:2] != expected:
            if tex.shape[:2] == (4, 4) and (tex == tex[0, 0]).all():
                tex = tex[:1, :1]
            else:
                raise ValueError("coefficient atlas dimensions do not match diffuse atlas: " + desc["path"])
        return {"texture": tex, "scale": np.array(desc["decode_scale"], np.float32),
                "bias": np.array(desc["decode_bias"], np.float32)}

    return {"model": spec.get("model", "affine"), "knot": spec.get("knot", 128 / 255),
            "locations": spec["locations"], "constant": load(spec["constant_atlas"]),
            "slopes": {loc: load(spec["slope_atlases"][loc]) for loc in spec["locations"]},
            "high_slopes": {loc: load(d) for loc, d in spec.get("high_slope_atlases", {}).items()},
            "probes": spec["probes"], "direct_palette": spec.get('direct_palette'),
            "palette": spec.get('palette')}


def reconstruction_colors(dest, reconstruction):
    if reconstruction.get('direct_palette'):
        return {loc: np.asarray(rgb, np.float32) / 255 for loc, rgb in reconstruction['palette'].items()}
    colors = {}
    for probe in reconstruction["probes"]:
        x, y, w, h = probe["rect"]
        encoded = bilinear(dest, np.array(x + w / 2), np.array(y + h / 2))[..., :3]
        colors[probe["location"]] = np.clip((encoded - probe["bias"]) / probe["gain"], 0, 1)
    return colors


def reconstructed_texture(dest, reconstruction):
    """Cache the fixed-palette response before bilinear sampling.

    Palette weights are spatially constant, so affine texture decoding, weighted response sums, and
    bilinear sampling commute. This gives the shader's result without seven bilinear gathers per layer
    on every CPU validation frame; color transforms and source-over still happen per layer afterwards.
    """
    colors = reconstruction_colors(dest, reconstruction)
    key = tuple(float(c) for loc in reconstruction["locations"] for c in colors[loc])
    if reconstruction.get("cached_palette") == key:
        return reconstruction["cached_texture"]
    constant = reconstruction["constant"]
    texture = constant["texture"].copy()
    texture[..., :3] = texture[..., :3] * constant["scale"] + constant["bias"]
    for loc in reconstruction["locations"]:
        slope = reconstruction["slopes"][loc]
        lo = slope["texture"][..., :3] * slope["scale"] + slope["bias"]
        if reconstruction["model"] == "piecewise_linear":
            high = reconstruction["high_slopes"][loc]
            hi = high["texture"][..., :3] * high["scale"] + high["bias"]
            knot = reconstruction["knot"]
            texture[..., :3] += lo * np.minimum(colors[loc], knot) + hi * np.maximum(colors[loc] - knot, 0)
        else:
            texture[..., :3] += lo * colors[loc]
    reconstruction["cached_palette"] = key
    reconstruction["cached_texture"] = texture
    return texture


def flash_blend(b, s, mode):
    """FlashRig's blend modes on straight colours (b = layers below, s = this layer)."""
    if mode == 3:
        return b * s
    if mode == 4:
        return b + s - b * s
    if mode == 5:
        return np.maximum(b, s)
    if mode == 6:
        return np.minimum(b, s)
    if mode == 7:
        return np.abs(b - s)
    if mode == 8:
        return np.minimum(b + s, 1)
    if mode == 9:
        return np.maximum(b - s, 0)
    lo, hi = 2 * b * s, 1 - 2 * (1 - b) * (1 - s)
    if mode == 13:
        return np.where(b >= 0.5, hi, lo)
    return np.where(s >= 0.5, hi, lo)  # 14 hardlight


def shade_frame(dest, sprite, layers, frame, tint, reconstruction=None, oversample=1, morph_frame=0):
    """FlashRig shader over the base sprite's rect for one frame -> straight RGBA (h*o, w*o, 4).
    oversample: evaluate o x o fragments per base texel (weapons/pets use a reduced-resolution base quad)."""
    x, y, w, h = sprite["rect"]
    o = oversample
    ys, xs = np.mgrid[0:h * o, 0:w * o]
    PX = x + (xs + 0.5) / o
    PY = y + h - (ys + 0.5) / o  # atlas px, y up
    acc = np.zeros(PX.shape + (3,), np.float32)  # premultiplied
    acc_a = np.zeros(PX.shape, np.float32)
    source = reconstructed_texture(dest, reconstruction) if reconstruction is not None else dest
    saved = None
    for L in layers:
        if L.get('morph_frame', -1) >= 0 and L['morph_frame'] != morph_frame:
            continue
        if L.get('group_start'):
            if saved is not None:
                raise ValueError('Nested blend group is unsupported')
            saved = acc, acc_a
            acc, acc_a = np.zeros_like(acc), np.zeros_like(acc_a)
        k = L["keys"][frame % len(L["keys"])]
        a, b, c, d, tx, ty = k[:6]
        cm, ca = np.array(k[6:10], np.float32), np.array(k[10:14], np.float32)
        qx = a * PX + b * PY + tx
        qy = c * PX + d * PY + ty
        rx, ry, rw, rh = k[14:18] if len(k) >= 18 else L["rect"]
        inside = (qx >= 0) & (qy >= 0) & (qx <= rw) & (qy <= rh)
        iy, ix = np.nonzero(inside)
        qxi, qyi = qx[iy, ix], qy[iy, ix]
        smp = bilinear(source, rx + np.clip(qxi, 0.5, rw - 0.5), ry + np.clip(qyi, 0.5, rh - 0.5))
        rgb = np.clip(smp[..., :3] * cm[:3] + ca[:3], 0, 1)
        al = np.clip(smp[..., 3] * cm[3] + ca[3], 0, 1)
        mode = int(L.get("blend") or 0)
        acc_i, acc_ai = acc[iy, ix], acc_a[iy, ix]
        if mode:
            cb = np.where(acc_ai[..., None] > 1e-4, acc_i / np.maximum(acc_ai, 1e-6)[..., None], 0)
            blended = np.clip(flash_blend(cb, rgb, mode), 0, 1)
            rgb = rgb + (blended - rgb) * acc_ai[..., None]
        acc[iy, ix] = acc_i * (1 - al[..., None]) + rgb * al[..., None]
        acc_a[iy, ix] = acc_ai * (1 - al) + al
        if L.get('group_end', -1) >= 0:
            if saved is None:
                raise ValueError('Unbalanced blend group')
            bg, ba = saved
            src = acc / np.maximum(acc_a, 1e-6)[..., None]
            dst = bg / np.maximum(ba, 1e-6)[..., None]
            mixed = np.clip(flash_blend(dst, src, L['group_end']), 0, 1)
            src = src + (mixed - src) * ba[..., None]
            acc = bg * (1 - acc_a[..., None]) + src * acc_a[..., None]
            acc_a = ba * (1 - acc_a) + acc_a
            saved = None
    if saved is not None:
        raise ValueError('Unclosed blend group')
    rgb = np.where(acc_a[..., None] > 0, acc / np.maximum(acc_a, 1e-6)[..., None], 0) * np.array(tint[:3])
    return np.concatenate([rgb, acc_a[..., None]], axis=2)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("swf_m")
    ap.add_argument("swf_f")
    ap.add_argument("--frames", type=int, default=0, help="frames per part to compare (0 = longest period)")
    ap.add_argument('--samples',help='Selected absolute source ticks, including late loop boundaries')
    ap.add_argument("--sheet", action="store_true")
    ap.add_argument("--parts", help="comma-separated front slot names to validate")
    ap.add_argument("--palette", choices=("default", "black", "grey", "white", "dark-skin", "light-skin"), default="default")
    args = ap.parse_args()
    data = json.load(open(os.path.join(args.out, "armor.json")))
    report = {}
    palette = dict(PALETTE)
    if args.palette in ('dark-skin','light-skin'):
        palette['Skin']=(65,40,25) if args.palette=='dark-skin' else (255,225,200)
    elif args.palette != "default":
        value = {"black": 0, "grey": 128, "white": 255}[args.palette]
        palette.update({loc: (value, value, value) for loc in ("Base", "Trim", "Accessory")})
    colors = {loc: np.asarray(rgb, np.float32) / 255 for loc, rgb in palette.items()}
    for g, swf in (("m", args.swf_m), ("f", args.swf_f)):
        gd = data["genders"][g]
        atlas = np.asarray(Image.open(os.path.join(args.out, gd["atlas"])).convert("RGBA"))
        dest = composite(atlas, args.out, gd, colors)
        reconstruction = load_reconstruction(args.out, gd)
        if reconstruction is not None and reconstruction.get('direct_palette'):
            reconstruction['palette'] = palette
        m = sp.load(swf)
        cc = ccscripts.cc_chars(m, swf, os.path.join(args.out, "work", g + "_cache"))
        if data.get('source',{}).get('faithful_effects'):
            from pathlib import Path
            from timeline_scripts import lower_initial_stops
            lower_initial_stops(m, Path(args.out) / 'work' / (g + '_cache'))
        locs = sorted({loc for loc, _ in cc.values()})
        sprites = {s["name"]: s for s in gd["sprites"]}
        for suffix, front, back, k in [*PARTS, *([MORPH_HEAD] if data.get('morph') else [])]:
            if args.parts and front not in args.parts.split(","):
                continue
            r = gd["renderers"].get(front)
            if not r:
                continue
            cid = [c for c, n in m.symbols.items() if "." not in n and n.endswith(g.upper() + suffix)][0]
            periods = [L["period"] for L in (r["layers"] or [])] or [1]
            n = args.frames or max(periods)
            samples=sorted(set(map(int,args.samples.split(',')))) if args.samples else list(range(n))
            if not samples or samples[0]<0 or samples[-1]>=65535:ap.error('Invalid source tick samples')
            job = {"items": [{"char": cid, "matrix": render.IDENT}], "variant": {l: palette[l] for l in locs if l in palette},
                   "vkey": args.palette, "frames": n}
            if args.samples:
                ref=render.render_samples(m,cc,job,samples,k,os.path.join(args.out,'work',g+'_validate',suffix+'_'+args.palette+'_sparse'))
            else:
                ref = render.render_jobs(m, cc, [job], k, os.path.join(args.out, "work", g + "_validate"),
                                         suffix + "_" + args.palette)[0]
            spr = sprites[r["sprite"]]
            # base canvas origin in twips: from pivot (registration point)
            x, y, w, h = spr["rect"]
            tb = 20.0 / k
            origin = (-spr["pivot"][0] * w * tb, -(1 - spr["pivot"][1]) * h * tb)
            errs = []
            sheet_frames = []
            for f in samples:
                if r["layers"]:
                    sim = shade_frame(dest, spr, r["layers"], f, r.get("tint", [1, 1, 1, 1]), reconstruction)
                else:
                    H = dest.shape[0]
                    sim = dest[H - (y + h):H - y, x:x + w].copy()
                    sim[..., 3] = np.where(sim[..., 3] < 4 / 255, 0, sim[..., 3])
                refimg = np.asarray(ref["images"][f], np.float32) / 255
                ref_origin=ref['origins'][f] if args.samples else ref['origin']
                ox = int(round((ref_origin[0] - origin[0]) / tb))
                oy = int(round((ref_origin[1] - origin[1]) / tb))
                canvas = np.zeros_like(sim)
                rh, rw = refimg.shape[:2]
                x0, y0 = max(0, ox), max(0, oy)
                x1, y1 = min(w, ox + rw), min(h, oy + rh)
                canvas[y0:y1, x0:x1] = refimg[y0 - oy:y1 - oy, x0 - ox:x1 - ox]
                outside = refimg[..., 3].sum() - canvas[..., 3].sum()
                # premultiplied difference, ignoring the invisible coverage alpha
                sp_ = sim[..., :3] * sim[..., 3:4]
                rp = canvas[..., :3] * canvas[..., 3:4]
                diff = np.abs(sp_ - rp).max(axis=2)
                da = np.abs(sim[..., 3] - canvas[..., 3])
                vis = (sim[..., 3] > 0.02) | (canvas[..., 3] > 0.02)
                errs.append({"mean": float(diff[vis].mean() * 255) if vis.any() else 0,
                             "p99": float(np.percentile(diff[vis], 99) * 255) if vis.any() else 0,
                             "alpha_mean": float(da[vis].mean() * 255) if vis.any() else 0,
                             "clipped_alpha": float(outside)})
                if args.sheet and f in (0, n // 3, 2 * n // 3):
                    sheet_frames.append((sim, canvas))
            worst = max(errs, key=lambda e: e["mean"])
            report["%s/%s" % (g, front)] = {"frames": len(samples), "sample_ticks":samples, "mean_err": round(float(np.mean([e["mean"] for e in errs])), 2),
                                            "worst_frame_mean": round(worst["mean"], 2),
                                            "p99_worst": round(max(e["p99"] for e in errs), 1),
                                            "alpha_mean": round(float(np.mean([e["alpha_mean"] for e in errs])), 2),
                                            "clipped": round(max(e["clipped_alpha"] for e in errs), 1),
                                            "palette": args.palette, "layer_periods": periods,
                                            "joint_period": math.lcm(*periods),
                                            "covers_joint_period": len(samples)>=math.lcm(*periods) and all(f==i for i,f in enumerate(samples)),
                                            "reference": "JPEXS original source with CC substitutions"}
            print("%s/%-13s frames %3d  mean %.2f  worst %.2f  p99 %.1f  alpha %.2f  clipped %.1f" % (
                g, front, len(samples), report["%s/%s" % (g, front)]["mean_err"], worst["mean"],
                report["%s/%s" % (g, front)]["p99_worst"], report["%s/%s" % (g, front)]["alpha_mean"],
                report["%s/%s" % (g, front)]["clipped"]), flush=True)
            if args.sheet and sheet_frames:
                tiles = []
                for sim, ref_c in sheet_frames:
                    for im in (sim, ref_c):
                        bg = np.full(im.shape[:2] + (3,), 0.35, np.float32)
                        tiles.append(im[..., :3] * im[..., 3:4] + bg * (1 - im[..., 3:4]))
                row = np.concatenate([np.pad(t, ((0, 0), (0, 6), (0, 0)), constant_values=1) for t in tiles], axis=1)
                Image.fromarray((np.clip(row, 0, 1) * 255).astype(np.uint8)).save(
                    os.path.join(args.out, "validate_%s_%s.png" % (g, front)))
    filename = "validation.json" if args.palette == "default" else "validation_%s.json" % args.palette
    json.dump(report, open(os.path.join(args.out, filename), "w"), indent=1)


if __name__ == "__main__":
    main()
