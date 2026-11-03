"""Convert a single animated Flash symbol (a weapon; also pets and other one-piece items) into FlashRig build input.

usage: python weapon.py <swf> <linkage> <out dir> --prefab <PrefabName> [--k 1.2] [--validate-frames 150]

Output matches armor.py's armor.json shape with one "gender" ("w"), so build_armor.py builds it:
one prefab whose root SpriteRenderer draws every layer of the symbol (bands, tracks, flipbooks, Flash blend
modes) with the FlashRig shader, animated by an Animator; no colour custom, so layers sample the bundle's own
atlas directly. Weapons: the client instantiates the prefab under the hand's weapon bone; native weapon sprites
are 1.2 px per Flash px with the pivot on the symbol's registration point (measured on Caladbolg 2709).
"""
import argparse
import hashlib
import json
import os

import numpy as np
from PIL import Image

import render
import swfparse as sp
from armor import GenderBuild, pack, pot, renderer_stack, PALETTE, write_reconstruction_atlases, downsample_image

WEAPON_K = 1.2
BASE_DOWNSAMPLE = 4  # the base image is only the quad (and a fallback look): store it at 1/4 resolution


def convert(swf, linkage, out, prefab, k=WEAPON_K, name=None, log=print, rgb_cc=False,
            expanded=False, max_period=960, max_layers=32, faithful_effects=False, color_mapping=None,
            interactive_morph=False, source_flipbook=False):
    if interactive_morph:
        expanded, faithful_effects = True, True
    os.makedirs(out, exist_ok=True)
    m = sp.load(swf)
    cid = next((c for c, n in m.symbols.items() if n == linkage), None)
    if cid is None:
        raise SystemExit("linkage %s not found; exported symbols: %s" % (linkage, sorted(m.symbols.values())[:20]))
    import rig
    rig.MAX_PERIOD = max_period
    if rgb_cc or interactive_morph:
        import ccscripts
        source_cc = ccscripts.cc_chars(m, swf, os.path.join(out, 'work', 'w_cache'))
        rgb_cc = rgb_cc or bool(source_cc)
    if rgb_cc:
        locations = {location for location, shade in source_cc.values()}
        if color_mapping is None:
            # Preserve the already published spear encoding; other sources use
            # their authored locations, including skin/hair/eyes on morph helms.
            if 'Eye' in locations and locations <= {'Eye', 'Accessory'}:
                color_mapping = {'Base': 'Eye', 'Trim': 'Trim', 'Accessory': 'Accessory'}
            else:
                if len(locations) > 3:
                    color_mapping = {loc: loc for loc in PALETTE}
                else:
                    ordered = sorted(locations, key=lambda loc: list(PALETTE).index(loc))
                    color_mapping = dict(zip(('Base', 'Trim', 'Accessory'), ordered))
                    for slot in ('Base', 'Trim', 'Accessory'):
                        color_mapping.setdefault(slot, slot)
    color_map = {source: slot for slot, source in color_mapping.items()} if rgb_cc else None
    palette = {slot: PALETTE[source] for slot, source in color_mapping.items()} if rgb_cc else None
    coefficient_locations = tuple(color_mapping) if rgb_cc else ('Base', 'Trim', 'Accessory')
    gb = GenderBuild(swf, "w", os.path.join(out, "work"), log, pieces=[("Piece", prefab, None, k, cid)],
                     colour=rgb_cc or interactive_morph, rgb_cc=rgb_cc, expanded=expanded, color_map=color_map,
                     palette=palette, max_layers=max_layers, faithful_effects=faithful_effects,
                     rgb_locations=coefficient_locations, interactive_morph=interactive_morph,
                     source_flipbook=source_flipbook)
    unsupported = set(gb.locs) - set(coefficient_locations)
    if unsupported:
        raise ValueError('Weapon RGB palette needs explicit mapping for ' + str(unsupported))
    parts = gb.run()
    part = parts[0]
    if part["layers"]:
        b = part["base"]
        f = BASE_DOWNSAMPLE
        h, w = b["diffuse"].shape[:2]
        small = Image.fromarray(b["diffuse"]).resize((max(1, -(-w // f)), max(1, -(-h // f))), Image.BOX)
        b["diffuse"], b["tpp"], b["downsample"] = np.asarray(small), b["tpp"] * f, f
    images = []
    for key, im in part["images"].items():
        images.append(("_".join(str(x).replace(".", "p") for x in key[1:]), im))
        im["id"] = images[-1][0]
    images.append(("base", part["base"]))
    part["base"]["id"] = "base"
    if rgb_cc or interactive_morph:
        unique, seen = [], {}
        for iid, im in images:
            digest = hashlib.sha256(im['diffuse'].tobytes())
            response = im.get('response')
            if response:
                digest.update(response['constant'].tobytes())
                digest.update(response['alpha'].tobytes())
                for kind in ('slopes', 'high_slopes'):
                    for loc, array in sorted(response[kind].items()):
                        digest.update(loc.encode()); digest.update(array.tobytes())
            signature = im['diffuse'].shape, im.get('downsample', 1), digest.digest()
            if iid != 'base' and signature in seen:
                im['id'] = seen[signature]
            else:
                seen[signature] = iid
                unique.append((iid, im))
        log('deduplicated %d images to %d' % (len(images), len(unique)))
        images = unique
    downsample_passes = 0
    bounded_atlas = interactive_morph or (expanded and faithful_effects)
    while True:
        sizes = [(im["diffuse"].shape[1], im["diffuse"].shape[0]) for _, im in images]
        widths = (512, 1024, 2048, 4096)
        full_maps = 2 + 2 * len(gb.locs) if rgb_cc else 1
        chosen = None
        for width in widths:
            pos, height = pack(sizes, width)
            if pos is None or (height > width and width < 4096) or height > 4096:
                continue
            # Client memory budget (ArmorBuilder.CheckTextureBudget): <= 4096 px, <= 24 MiB of BC7 maps.
            if bounded_atlas and width * (-(-height // 4) * 4) * full_maps > 24 * 1024 * 1024:
                continue
            chosen = width, height, pos
            break
        if chosen is not None:
            width, height, pos = chosen
            break
        if not bounded_atlas or downsample_passes >= 3:
            raise ValueError('Weapon atlas exceeds texture dimension or memory budget')
        for im in part['images'].values():
            downsample_image(im, 2)
        downsample_passes += 1
        log('aggregate atlas budget: image resolution reduced by another factor 2; geometry preserved')
    aw, ah = width, -(-height // 4) * 4  # no mipmaps, so any multiple of 4 works for BC7
    atlas = np.zeros((ah, aw, 4), np.uint8)
    sprites = {}
    for (iid, im), (x, y) in zip(images, pos):
        h, w = im["diffuse"].shape[:2]
        atlas[y:y + h, x:x + w] = im["diffuse"]
        sprites[iid] = {"name": "%s_%s" % (prefab, iid), "rect": [x, ah - (y + h), w, h], "pil": [x, y, w, h],
                        "pivot": [0.5, 0.5], "ppu": 100 / im.get("downsample", 1), "mask": None}
    b = part["base"]
    e = sprites["base"]
    e["pivot"] = [(-b["origin"][0] / b["tpp"]) / e["rect"][2], 1 - (-b["origin"][1] / b["tpp"]) / e["rect"][3]]
    Image.fromarray(atlas).save(os.path.join(out, "w_atlas.png"))
    stack = renderer_stack(part, b, sprites, ah, None)
    node = {"name": prefab, "parent": -1, "active": True, "pos": [0, 0, 0], "rot": [0, 0, 0, 1], "scale": [1, 1, 1],
            "slot": -1, "hasRenderer": True, "order": 0, "sortingLayer": 0, "rendererEnabled": True,
            "color": [1, 1, 1, 1]}
    gd = {"atlas": "w_atlas.png", "atlas_size": [aw, ah], "sprites": list(sprites.values()), "itemslots": [],
          "renderers": {prefab: stack}, "nodes": [node], "prefab_name": prefab, "atlas_format": "BC7",
          "mesh_type": "FullRect"}
    if rgb_cc:
        gd['color_reconstruction'] = write_reconstruction_atlases('w', parts, sprites, aw, ah, out, compact=True,
                                                                locations=coefficient_locations)
        gd['color_reconstruction'].update(probes=[], direct_palette=color_mapping, coefficient_format='BC7',
            palette={loc: list(palette[loc]) for loc in coefficient_locations})
    layers = stack.get("layers") or []
    log("atlas %dx%d, %d images, %d layers (%s)" % (aw, ah, len(images), len(layers),
        ", ".join("%s%s" % (L.kind, "+" + str(L.blend) if L.blend else "") for L in part["layers"])))
    data = {"name": name or prefab, "fps": int(round(m.fps)) or 24, "genders": {"w": gd}, "notes": gb.rig.notes,
            "source": {"swf": os.path.abspath(swf), "linkage": linkage, "k": k,
                       'rgb_cc': rgb_cc, 'expanded': expanded, 'max_period': max_period,
                       'color_map': color_map, 'faithful_effects': faithful_effects, 'source_flipbook': source_flipbook,
                       'timeline_programs': gb.timeline_programs, 'interactive_morph': interactive_morph,
                       'interaction_render_fallbacks': gb.interaction.render_fallbacks if gb.interaction else []}}
    data['source']['atlas_downsample_passes'] = downsample_passes
    json.dump(data, open(os.path.join(out, "armor.json"), "w"), indent=1)
    return data


def validate(out, frames, step=1, samples=None, palette=None, report_name='validation', oversample=None):
    """Compare the FlashRig emulation (numpy, same maths as the shader) with JPEXS renders of the original
    symbol, frame by frame, nested timelines playing."""
    import validate_armor as va
    data = json.load(open(os.path.join(out, "armor.json")))
    src = data["source"]
    m = sp.load(src["swf"])
    import ccscripts
    cc = ccscripts.cc_chars(m, src['swf'], os.path.join(out, 'work', 'reference_cc')) if src.get('rgb_cc') or src.get('interactive_morph') else {}
    interaction = None
    if src.get('interactive_morph'):
        from interactive_morph import Compiler
        interaction = Compiler(m, os.path.join(out, 'work', 'reference_cc'))
    elif src.get('faithful_effects'):
        from pathlib import Path
        from timeline_scripts import lower_initial_stops
        lower_initial_stops(m, Path(out) / 'work' / 'w_cache')
    cid = next(c for c, n in m.symbols.items() if n == src["linkage"])
    gd = data["genders"]["w"]
    k = src["k"]
    palette = palette or PALETTE
    job = {"items": [{"char": cid, "matrix": render.IDENT}], "variant": palette if cc else None,
           "vkey": "ref", "frames": frames}
    samples = samples if samples is not None else list(range(0, frames, step))
    if interaction:
        controls = interaction.reachable_controls(cid)
        if len(controls) != 1:
            raise ValueError('Interactive weapon needs exactly one source control')
        control = controls.pop()
        states = list(range(len(interaction.original.chars[control].frames)))
        jobs = [dict(job, items=[dict(char=interaction.clone_pose(cid, control, state), matrix=render.IDENT)],
                     frames=max(samples) + 1, group=state) for state in states]
        references = {}
        for state, pose_job in zip(states, jobs):
            references[state] = render.render_samples(m, cc, pose_job, samples, k,
                os.path.join(out, 'work', report_name, 'state_' + str(state)))
            print('source pose', state + 1, '/', len(states), 'captured', len(samples), 'sparse frames', flush=True)
    else:
        states = [0]
        references = {0: render.render_samples(m, cc, job, samples, k, os.path.join(out, 'work', report_name))}
    reconstruction = va.load_reconstruction(out, gd)
    if reconstruction and reconstruction.get('direct_palette'):
        reconstruction['palette'] = {loc: palette[source] for loc, source in reconstruction['direct_palette'].items()}
    atlas = np.asarray(Image.open(os.path.join(out, gd["atlas"])).convert("RGBA"), np.float32) / 255
    r = next(iter(gd["renderers"].values()))
    spr = {s["name"]: s for s in gd["sprites"]}[r["sprite"]]
    x, y, w, h = spr["rect"]
    full_over = int(round(100 / spr["ppu"]))
    over = full_over if oversample is None else oversample
    tb = 20.0 / k
    origin = (-spr["pivot"][0] * w * full_over * tb, -(1 - spr["pivot"][1]) * h * full_over * tb)
    report, previews = [], []
    for state, f in ((state, f) for state in states for f in samples):
        ref = references[state]
        if r.get("layers"):
            sim = va.shade_frame(atlas, spr, r["layers"], f, r.get("tint", [1, 1, 1, 1]), oversample=over,
                                 reconstruction=reconstruction, morph_frame=state)
        else:
            H = atlas.shape[0]
            sim = atlas[H - (y + h):H - y, x:x + w].copy()
        refimg = np.asarray(ref["images"][f], np.float32) / 255
        ox = int(round((ref["origins"][f][0] - origin[0]) / tb))
        oy = int(round((ref["origins"][f][1] - origin[1]) / tb))
        canvas = np.zeros((h * full_over, w * full_over, 4), np.float32)
        rh, rw = refimg.shape[:2]
        x0, y0, x1, y1 = max(0, ox), max(0, oy), min(canvas.shape[1], ox + rw), min(canvas.shape[0], oy + rh)
        canvas[y0:y1, x0:x1] = refimg[y0 - oy:y1 - oy, x0 - ox:x1 - ox]
        clipped = float(refimg[..., 3].sum() - canvas[..., 3].sum())
        if over != full_over:
            canvas = np.asarray(Image.fromarray((canvas * 255 + 0.5).astype(np.uint8)).resize(
                (w * over, h * over), Image.Resampling.BOX), np.float32) / 255
        spm = sim[..., :3] * sim[..., 3:4]
        rpm = canvas[..., :3] * canvas[..., 3:4]
        d = np.abs(spm - rpm).max(axis=2) * 255
        vis = (sim[..., 3] > 0.02) | (canvas[..., 3] > 0.02)
        report.append({"state": state, "frame": f, "mean": round(float(d[vis].mean()), 2) if vis.any() else 0,
                       "p99": round(float(np.percentile(d[vis], 99)), 1) if vis.any() else 0,
                       "clipped_alpha": round(clipped, 1)})
        if len(previews) < 3 and f in (samples[0], samples[len(samples) // 3], samples[2 * len(samples) // 3]):
            previews.append((sim, canvas))
    tiles = []
    for sim, can in previews:
        for im in (sim, can):
            bg = np.full(im.shape[:2] + (3,), 0.2, np.float32)
            tiles.append(im[..., :3] * im[..., 3:4] + bg * (1 - im[..., 3:4]))
    row = np.concatenate([np.pad(t, ((0, 0), (0, 8), (0, 0)), constant_values=1) for t in tiles], axis=1)
    Image.fromarray((np.clip(row, 0, 1) * 255).astype(np.uint8)).save(os.path.join(out, report_name + '_sheet.png'))
    summary = {"frames": len(report), "mean": round(float(np.mean([r_["mean"] for r_ in report])), 2),
               'oversample': over, 'samples': samples, 'states': states,
               "worst_mean": max(r_["mean"] for r_ in report), "worst_p99": max(r_["p99"] for r_ in report),
               "max_clipped_alpha": max(r_["clipped_alpha"] for r_ in report), "per_frame": report}
    json.dump(summary, open(os.path.join(out, report_name + '.json'), "w"), indent=1)
    print("validation: %d frames, mean %.2f, worst frame mean %.2f, worst p99 %.1f, clipped %.1f" % (
        summary["frames"], summary["mean"], summary["worst_mean"], summary["worst_p99"], summary["max_clipped_alpha"]))
    return summary


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("swf")
    ap.add_argument("linkage")
    ap.add_argument("out")
    ap.add_argument("--prefab", required=True)
    ap.add_argument("--name")
    ap.add_argument("--k", type=float, default=WEAPON_K)
    ap.add_argument("--validate-frames", type=int, default=0)
    ap.add_argument('--validate-samples', help='Explicit source frame indices, including late loop boundaries')
    ap.add_argument('--rgb-cc', action='store_true')
    ap.add_argument('--expanded', action='store_true')
    ap.add_argument('--faithful-effects', action='store_true')
    ap.add_argument('--interactive-morph', action='store_true', help='Compile source click-to-play forms and their independent child clocks')
    ap.add_argument('--source-flipbook', action='store_true', help='Bake the complete faithful noninteractive source over its exact uncapped loop')
    ap.add_argument('--max-period', type=int, default=960)
    ap.add_argument('--max-layers', type=int, default=32)
    args = ap.parse_args()
    samples = [int(f) for f in args.validate_samples.split(',')] if args.validate_samples else None
    if samples is not None and (args.validate_frames or not samples or any(f < 0 for f in samples) or len(samples) != len(set(samples))):
        ap.error('Use unique nonnegative validate-samples or validate-frames, not both')
    convert(args.swf, args.linkage, args.out, args.prefab, args.k, args.name,
            rgb_cc=args.rgb_cc, expanded=args.expanded, max_period=args.max_period, max_layers=args.max_layers,
            faithful_effects=args.faithful_effects, interactive_morph=args.interactive_morph,
            source_flipbook=args.source_flipbook)
    if samples is not None:
        validate(args.out, max(samples) + 1, samples=samples)
    elif args.validate_frames:
        validate(args.out, args.validate_frames)


if __name__ == "__main__":
    main()
