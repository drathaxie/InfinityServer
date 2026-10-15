"""Convert a Flash helm or head morph into the client's native helm slot hierarchy."""
import argparse
import json
from pathlib import Path

import weapon
from armor import SLOT_ID, PALETTE


def convert(source, linkage, out, prefab='HelmGO', name=None, k=1.645,
            rgb_cc=False, max_period=10000, six_colors=False, interactive_morph=False):
    # Native setupHelm reparents these three named direct children and keeps
    # their Animator/material. The inactive data root retains the ItemSlots.
    data = weapon.convert(source, linkage, str(out), prefab, k=k, name=name,
                          rgb_cc=rgb_cc, expanded=True, faithful_effects=True,
                          max_period=max_period, max_layers=128,
                          color_mapping={loc: loc for loc in PALETTE} if six_colors else None,
                          interactive_morph=interactive_morph)
    gd = data['genders']['w']
    gd['direct_atlas'] = True
    renderer = gd['renderers'].pop(prefab)
    gd['renderers']['helm'] = renderer
    root = dict(gd['nodes'][0], hasRenderer=False)
    children = []
    for slot in ('helm', 'fronthair', 'backhair'):
        children.append(dict(root, name=slot, parent=0, slot=SLOT_ID[slot],
                             hasRenderer=True, order=36 if slot != 'backhair' else -1))
    gd['nodes'] = [root, *children]
    gd['itemslots'] = [dict(slot=SLOT_ID['helm'], sprite=renderer['sprite'])]
    # Direct RGB reconstruction uses the source atlas and player palette, not
    # the stock helm's scalar CC material replacement.
    data['kind'] = 'helm'
    data['attachment_contract'] = dict(slots=['helm', 'fronthair', 'backhair'],
                                       k=k, alignment='registration point; requires player equip check')
    Path(out, 'armor.json').write_text(json.dumps(data, indent=1))
    return data


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('swf')
    p.add_argument('linkage')
    p.add_argument('out')
    p.add_argument('--name')
    p.add_argument('--prefab', default='HelmGO')
    p.add_argument('--k', type=float, default=1.645)
    p.add_argument('--rgb-cc', action='store_true')
    p.add_argument('--interactive-morph', action='store_true')
    p.add_argument('--six-colors', action='store_true', help='Use independent coefficients for all six native player color locations')
    p.add_argument('--validate-samples', default='0,1,7,29,59')
    a = p.parse_args()
    convert(a.swf, a.linkage, a.out, a.prefab, a.name, a.k, a.rgb_cc or a.six_colors,
            six_colors=a.six_colors, interactive_morph=a.interactive_morph)
    samples = sorted(set(map(int, a.validate_samples.split(','))))
    weapon.validate(a.out, max(samples) + 1, samples=samples)


if __name__ == '__main__':
    main()
