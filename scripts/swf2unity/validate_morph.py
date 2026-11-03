"""Compare every clickable pose with source display lists and loaded-bundle GPU captures."""
import argparse
import json
import math
from pathlib import Path

import numpy as np
from PIL import Image

import ccscripts
import interactive_morph as im
import render
import swfparse as sp
import validate_armor as va
from armor import PARTS, MORPH_HEAD, PALETTE


def error(actual, reference):
    visible = (actual[..., 3] > .02) | (reference[..., 3] > .02)
    diff = np.abs(actual[..., :3] * actual[..., 3:4] - reference[..., :3] * reference[..., 3:4]).max(2)
    return dict(mean=float(diff[visible].mean() * 255) if visible.any() else 0,
                p99=float(np.percentile(diff[visible], 99) * 255) if visible.any() else 0)


def compare_gpu(out, directory, frames=(0, 7, 19, 39, 86)):
    data = json.loads((out / 'armor.json').read_text())
    checks = []
    expected_captures = 0
    for gender, gd in data['genders'].items():
        atlas = np.asarray(Image.open(out / gd['atlas']).convert('RGBA'))
        dest = va.composite(atlas, str(out), gd) if gd['itemslots'] and not gd.get('direct_atlas') else atlas.astype(np.float32) / 255
        reconstruction = va.load_reconstruction(str(out), gd)
        sprites = {s['name']: s for s in gd['sprites']}
        for slot, renderer in gd['renderers'].items():
            if not renderer.get('morph'):
                continue
            expected_captures += renderer['morph']['frame_count'] * len(frames)
            for state in range(renderer['morph']['frame_count']):
                for frame in frames:
                    path = directory / f'{gender}_{slot}_m{state:03}_f{frame:03}.png'
                    assert path.is_file(), 'Missing activated-pose GPU capture: ' + str(path)
                    actual = np.asarray(Image.open(path).convert('RGBA'), np.float32) / 255
                    simulated = va.shade_frame(dest, sprites[renderer['sprite']], renderer['layers'], frame,
                        renderer['tint'], reconstruction, morph_frame=state)
                    expected = np.concatenate([simulated[..., :3] * simulated[..., 3:4], simulated[..., 3:4]], axis=2)
                    assert actual.shape == expected.shape
                    diff = np.abs(actual - expected).max(2) * 255
                    checks.append(dict(gender=gender, slot=slot, state=state, frame=frame,
                        mean=float(diff.mean()), p99=float(np.percentile(diff, 99)), maximum=float(diff.max())))
    result = dict(status='passed' if all(c['mean'] <= 2 and c['p99'] <= 24 for c in checks) else 'failed',
        compared=len(checks), expected=expected_captures, worst_mean=max(c['mean'] for c in checks),
        worst_p99=max(c['p99'] for c in checks), checks=checks)
    (directory.parent / 'morph_gpu_validation.json').write_text(json.dumps(result, indent=2))
    assert result['status'] == 'passed' and result['compared'] == result['expected'], result
    print('ACTIVATED_MORPH_GPU_PASSED', result['compared'], result['worst_mean'], result['worst_p99'], flush=True)
    return result


def validate(out, sources, gpu=None, frames=(0, 7, 19, 39, 86), parts=None):
    data = json.loads((out / 'armor.json').read_text())
    reports, gpu_reports = {}, []
    for gender, source in sources.items():
        gd = data['genders'][gender]
        atlas = np.asarray(Image.open(out / gd['atlas']).convert('RGBA'))
        dest = va.composite(atlas, str(out), gd)
        reconstruction = va.load_reconstruction(str(out), gd)
        movie = sp.load(str(source))
        cache = out / 'work' / (gender + '_cache')
        cc = ccscripts.cc_chars(movie, str(source), str(cache))
        compiler = im.Compiler(movie, cache)
        sprites = {s['name']: s for s in gd['sprites']}
        for suffix, slot, _, scale in [*PARTS, MORPH_HEAD]:
            if parts and slot not in parts:
                continue
            renderer = gd['renderers'].get(slot)
            if renderer is None:
                continue
            root = next(cid for cid, name in movie.symbols.items()
                        if '.' not in name and name.endswith(gender.upper() + suffix))
            control = compiler.reachable_controls(root)
            control = next(iter(control)) if control else None
            count = renderer.get('morph', {}).get('frame_count', 1)
            period = max([layer['period'] for layer in renderer.get('layers') or []] or [1])
            samples = sorted({frame % period for frame in frames})
            jobs = []
            for state in range(count):
                pose = compiler.clone_pose(root, control, state) if control is not None else root
                jobs.append(dict(items=[dict(char=pose, matrix=render.IDENT)], variant=PALETTE,
                                 vkey='player', frames=max(samples) + 1))
            refs = render.render_jobs(movie, cc, jobs, scale, str(out / 'work' / 'morph_reference'), gender + '_' + slot)
            sprite = sprites[renderer['sprite']]
            _, _, width, height = sprite['rect']
            tpp = 20 / scale
            origin = (-sprite['pivot'][0] * width * tpp, -(1 - sprite['pivot'][1]) * height * tpp)
            checks = []
            for state, ref in enumerate(refs):
                for frame in samples:
                    actual = va.shade_frame(dest, sprite, renderer['layers'], frame, renderer['tint'],
                                            reconstruction, morph_frame=state)
                    source_image = np.asarray(ref['images'][frame], np.float32) / 255
                    canvas = np.zeros_like(actual)
                    # render_jobs crops a fixed footprint for the entire job.
                    ref_origin = ref.get('origins', [ref['origin']])[frame]
                    ox, oy = (int(round((ref_origin[i] - origin[i]) / tpp)) for i in range(2))
                    rh, rw = source_image.shape[:2]
                    x0, y0 = max(0, ox), max(0, oy)
                    x1, y1 = min(width, ox + rw), min(height, oy + rh)
                    if x1 > x0 and y1 > y0:
                        canvas[y0:y1, x0:x1] = source_image[y0-oy:y1-oy, x0-ox:x1-ox]
                    clipped = max(0, float(source_image[..., 3].sum() - canvas[..., 3].sum()))
                    checks.append(dict(state=state, frame=frame, clipped=clipped, **error(actual, canvas)))
                    if gpu:
                        path = gpu / f'{gender}_{slot}_m{state:03}_f{frame:03}.png'
                        if path.exists():
                            pixels = np.asarray(Image.open(path).convert('RGBA'), np.float32) / 255
                            gpu_reports.append(dict(gender=gender, slot=slot, state=state, frame=frame,
                                                    **error(pixels, actual)))
            reports[gender + '/' + slot] = dict(frames=len(checks), states=count, child_period=period,
                mean_err=float(np.mean([c['mean'] for c in checks])),
                worst_frame_mean=max(c['mean'] for c in checks), clipped=max(c['clipped'] for c in checks),
                p99_worst=max(c['p99'] for c in checks), checks=checks,
                reference='Original SWF display lists at every explicit parent pose, independent child time')
            row = reports[gender + '/' + slot]
            print(gender, slot, 'states', count, 'samples', len(checks), 'worst', round(row['worst_frame_mean'], 3),
                  'clipped', row['clipped'], flush=True)
    (out / 'validation.json').write_text(json.dumps(reports, indent=2))
    assert all(row['worst_frame_mean'] <= 25 and row['clipped'] <= .5 for row in reports.values()), 'Source pose fidelity failed'
    if gpu:
        expected = sum(r['frames'] for r in reports.values() if r['states'] > 1)
        assert len(gpu_reports) == expected, ('Missing morph GPU captures', len(gpu_reports), expected)
        result = dict(status='passed', expected=expected, compared=len(gpu_reports),
                      worst_mean=max(r['mean'] for r in gpu_reports), worst_p99=max(r['p99'] for r in gpu_reports),
                      checks=gpu_reports)
        (gpu.parent / 'morph_gpu_validation.json').write_text(json.dumps(result, indent=2))
        assert result['worst_mean'] <= 2 and result['worst_p99'] <= 24, result
    return reports


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('out', type=Path)
    parser.add_argument('male', type=Path)
    parser.add_argument('female', type=Path)
    parser.add_argument('--gpu', type=Path)
    parser.add_argument('--parts', help='Comma-separated front slots for a focused diagnostic')
    parser.add_argument('--gpu-only', action='store_true', help='Check all activated GPU poses without repeating source rasterization')
    parser.add_argument('--frames', default='0,7,19,39,86', help='GPU capture frame indices')
    args = parser.parse_args()
    if args.gpu_only:
        assert args.gpu is not None
        frames = tuple(sorted(set(map(int, args.frames.split(',')))))
        assert frames and frames[0] >= 0
        compare_gpu(args.out, args.gpu, frames=frames)
    else:
        validate(args.out, {'m': args.male, 'f': args.female}, None,
                 parts=args.parts.split(',') if args.parts else None)
