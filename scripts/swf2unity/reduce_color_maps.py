"""Store a player-color conversion's slope coefficient maps as DXT1 instead of BC7 (half their memory).

usage: python reduce_color_maps.py <conversion dir> <new conversion dir>

The atlas and the constant map keep their format. Elemental Dracomancer already ships DXT1 slopes; the
rebuild's GPU-versus-emulation gate (mean <= 2/255, p99 <= 24/255) measures the compression error.
"""
import argparse
import json
from pathlib import Path
import shutil


def reduce(source, target):
    data = json.loads((source / 'armor.json').read_text())
    models = [gd.get('color_reconstruction') for gd in data['genders'].values()]
    assert all(models) and all(m.get('slope_coefficient_format', m.get('coefficient_format')) == 'BC7' for m in models)
    target.mkdir(parents=True, exist_ok=False)
    shutil.copytree(source, target, dirs_exist_ok=True, ignore=shutil.ignore_patterns('work', 'bundle'))
    for model in models:
        model['slope_coefficient_format'] = 'DXT1'
    data['source']['reduced_color_maps'] = dict(slope_coefficient_format='DXT1', source=str(source))
    (target / 'armor.json').write_text(json.dumps(data, indent=1))


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('source', type=Path)
    ap.add_argument('target', type=Path)
    args = ap.parse_args()
    reduce(args.source, args.target)


if __name__ == '__main__':
    main()
