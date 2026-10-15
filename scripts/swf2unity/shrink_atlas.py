"""Halve the layer-image resolution of an existing weapon conversion so its atlas fits the 4096 px budget.

usage: python shrink_atlas.py <conversion dir> <new conversion dir> [--samples 0,1,...]

This is weapon.py's own aggregate-atlas fallback (armor.downsample_image, factor 2, geometry preserved) applied
after the fact, so the source SWF does not have to be re-rendered. The base quad keeps its resolution; every
layer/flipbook image is box-filtered to half size and the atlas is repacked. Each layer key's image matrix is
rewritten for the new image scale and positions, so the rendered geometry is unchanged. The new conversion is
then validated against the Flash source exactly like a fresh conversion (weapon.validate).
"""
import argparse
import json
import math
from pathlib import Path
import shutil

import numpy as np
from PIL import Image

from armor import PAD, downsample_image, pack

MAX_TEXTURE_DIMENSION = 4096
MAX_TEXTURE_BYTES = 24 * 1024 * 1024  # BC7: one byte per pixel


def choose_layout(sizes):
    for width in (512, 1024, 2048, 4096):
        pos, height = pack(sizes, width)
        if pos is None or (height > width and width < MAX_TEXTURE_DIMENSION):
            continue
        height = -(-height // 4) * 4
        if height <= MAX_TEXTURE_DIMENSION and width * height <= MAX_TEXTURE_BYTES:
            return width, height, pos
    return None


def shrink(source, target, factor=2):
    data = json.loads((source / 'armor.json').read_text())
    assert list(data['genders']) == ['w'], 'Only single-piece weapon conversions are supported'
    gd = data['genders']['w']
    assert not gd.get('color_reconstruction') and not gd['itemslots'], 'Player-color atlases are not supported'
    assert gd['atlas_format'] == 'BC7' and len(gd['renderers']) == 1
    renderer = next(iter(gd['renderers'].values()))
    atlas = np.asarray(Image.open(source / gd['atlas']).convert('RGBA'))
    old_h = atlas.shape[0]
    assert [atlas.shape[1], old_h] == gd['atlas_size']
    sprites = {s['name']: s for s in gd['sprites']}
    base = sprites[renderer['sprite']]
    by_rect = {}
    for s in gd['sprites']:
        by_rect.setdefault(tuple(s['rect']), s['name'])
    images, padded_heights = {}, {}
    for s in gd['sprites']:
        x, y, w, h = s['pil']
        im = {'diffuse': atlas[y:y + h, x:x + w].copy(), 'tpp': 1.0}
        if s is not base:
            downsample_image(im, factor)
            padded_heights[s['name']] = im['diffuse'].shape[0] * factor
        images[s['name']] = im['diffuse']
    names = list(images)
    layout = choose_layout([(images[n].shape[1], images[n].shape[0]) for n in names])
    assert layout, 'Downsampled images still exceed the atlas budget'
    width, height, pos = layout
    new_atlas = np.zeros((height, width, 4), np.uint8)
    moved = {}
    for name, (x, y) in zip(names, pos):
        h, w = images[name].shape[:2]
        new_atlas[y:y + h, x:x + w] = images[name]
        old = sprites[name]
        moved[name] = dict(old, rect=[x, height - (y + h), w, h], pil=[x, y, w, h],
                           ppu=old['ppu'] if old is base else old['ppu'] / factor)
    # Base movement: an atlas pixel P' of the new base quad was P' + delta in the old atlas.
    bx, by = base['pil'][:2]
    nbx, nby = moved[base['name']]['pil'][:2]
    delta = np.array([bx - nbx, (old_h - by) - (height - nby)], np.float64)
    for layer in renderer['layers']:
        for key in layer['keys']:
            name = by_rect[(key[14] + 1, key[15] + 1, key[16] - 2, key[17] - 2)]
            assert name != base['name'], 'Layer samples the base quad'
            A = np.array([[key[0], key[1]], [key[2], key[3]]], np.float64)
            t = np.array([key[4] - 1, key[5] - 1], np.float64)
            # Image px (y up) of the half-size image: (Q + (0, H - h)) / factor, H = h padded to the factor.
            t = (A @ delta + t + np.array([0, padded_heights[name] - sprites[name]['pil'][3]])) / factor
            A = A / factor
            x, y, w, h = moved[name]['rect']
            key[:6] = [A[0, 0], A[0, 1], A[1, 0], A[1, 1], t[0] + 1, t[1] + 1]
            key[14:18] = [x - 1, y - 1, w + 2, h + 2]
        layer['rect'] = layer['keys'][0][14:18]
    base_new = moved[base['name']]
    assert base_new['pivot'] == base['pivot'] and base_new['rect'][2:] == base['rect'][2:]
    gd['sprites'] = [moved[s['name']] for s in gd['sprites']]
    gd['atlas_size'] = [width, height]
    data['source']['atlas_downsample_passes'] = data['source'].get('atlas_downsample_passes', 0) + 1
    data['source']['post_pack_downsample'] = dict(factor=factor, source=str(source), old_atlas_size=[atlas.shape[1], old_h])
    target.mkdir(parents=True, exist_ok=False)
    Image.fromarray(new_atlas).save(target / gd['atlas'])
    (target / 'armor.json').write_text(json.dumps(data, indent=1))
    return data


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('source', type=Path)
    ap.add_argument('target', type=Path)
    ap.add_argument('--factor', type=int, default=2)
    ap.add_argument('--samples', help='Source frames for weapon.validate; defaults to the original validation samples')
    args = ap.parse_args()
    data = shrink(args.source, args.target, args.factor)
    print('atlas', data['genders']['w']['atlas_size'], flush=True)
    original = json.loads((args.source / 'validation.json').read_text())
    samples = [int(f) for f in args.samples.split(',')] if args.samples else original['samples']
    # Reuse the Flash script cache so interactive sources compile exactly as before.
    for cache in ('w_cache', 'reference_cc'):
        if (args.source / 'work' / cache).is_dir():
            shutil.copytree(args.source / 'work' / cache, args.target / 'work' / cache)
    from weapon import validate
    summary = validate(str(args.target), max(samples) + 1, samples=samples)
    assert summary['worst_mean'] <= 25 and summary['max_clipped_alpha'] <= 0.5, summary


if __name__ == '__main__':
    main()
