"""Compare the spear's per-instance Unity palette captures with fixed RGB-response emulation."""
import argparse
import json
from pathlib import Path

import numpy as np
from PIL import Image
import validate_armor as va


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('conversion', type=Path)
    parser.add_argument('job', type=Path)
    args = parser.parse_args()
    job = json.loads(args.job.read_text())
    output = Path(job['output'])
    contract = json.loads((output / 'runtime_contract.json').read_text())
    assert contract['status'] == 'passed'
    assert all(contract[key] for key in ('independentInstances', 'materialsPreserved',
        'animationClocksPreserved', 'colorsSurviveAnimationUpdate'))
    data = json.loads((args.conversion / 'armor.json').read_text())
    gd = data['genders']['w']
    assert len(gd['renderers']) == 1
    renderer = next(iter(gd['renderers'].values()))
    sprite = next(s for s in gd['sprites'] if s['name'] == renderer['sprite'])
    atlas = np.asarray(Image.open(args.conversion / gd['atlas']).convert('RGBA'), np.float32) / 255
    reconstruction = va.load_reconstruction(str(args.conversion), gd)
    checks = []
    for palette in job['palettes']:
        source = dict(Base=palette.get('baseColor'), Trim=palette['trim'], Accessory=palette['accessory'],
                      Skin=palette.get('skin'), Hair=palette.get('hair'), Eye=palette['eye'])
        reconstruction['palette'] = {location: source[original] for location, original
                                      in reconstruction['direct_palette'].items()}
        sim = va.shade_frame(atlas, sprite, renderer['layers'], job['frame'],
            renderer.get('tint', [1, 1, 1, 1]), reconstruction=reconstruction)
        expected = np.concatenate([sim[..., :3] * sim[..., 3:4], sim[..., 3:4]], axis=2)
        actual = np.asarray(Image.open(output / (palette['name'] + '.png')).convert('RGBA'), np.float32) / 255
        assert actual.shape == expected.shape
        delta = np.abs(actual - expected).max(axis=2) * 255
        checks.append(dict(palette=palette['name'], frame=job['frame'],
            mean=float(delta.mean()), p99=float(np.percentile(delta, 99)), max=float(delta.max())))
    result = dict(status='passed' if all(c['mean'] <= 2 and c['p99'] <= 24 for c in checks) else 'failed',
        max_mean_error=2, max_p99_error=24, checks=checks, runtime_contract=contract,
        scope='Serialized bundle and per-instance property block; player equip still requires observation.')
    (output / 'validation_summary.json').write_text(json.dumps(result, indent=2))
    print(json.dumps(result))
    assert result['status'] == 'passed'


if __name__ == '__main__':
    main()
