"""Fuse the spear's four mutually exclusive copies without baking or shortening clocks."""
from copy import deepcopy
import hashlib
import json
import numpy as np
from trial_host import ROOT

p = ROOT / 'outputs/flash_converter_trial/dragongate/GiganteForceSpear/conversion/armor.json'
data = json.loads(p.read_text())
assert hashlib.sha256(open(data['source']['swf'], 'rb').read()).hexdigest() == '3c5e5d5912eb0bb730ab082ea0bacf4b27804d36f609560473992f02a367f628'
renderer = next(iter(data['genders']['w']['renderers'].values()))
layers = renderer['layers']
assert len(layers) == 114 and layers[52]['group_start'] and layers[99]['group_end'] == 8
blocks = [layers[start:start+12] for start in (52, 64, 76, 88)]
anchors = np.array([np.asarray(block[3]['keys'])[:, 9] > 0 for block in blocks])
assert anchors.shape == (4, 135) and anchors.sum(axis=0).max() == 1
merged, checks = [], []
for column in range(12):
    definitions = [block[column] for block in blocks]
    period = definitions[0]['period']
    assert period % 135 == 0 and all(d['period'] == period for d in definitions)
    assert len({d['blend'] for d in definitions}) == 1
    arrays = np.asarray([d['keys'] for d in definitions])
    assert arrays.shape == (4, period, 18) and (arrays[:, :, 13] == 0).all()
    gates = anchors[:, np.arange(period) % 135]
    # Every visible child is gated by its parent. Since 135 divides every child
    # period, this proves mutual exclusion for the entire joint timeline.
    assert not ((arrays[:, :, 9] > 0) & ~gates).any()
    selected = np.argmax(gates, axis=0)
    keys = arrays[selected, np.arange(period)].copy()
    keys[~gates.any(axis=0), 9] = 0
    definition = deepcopy(definitions[0])
    definition.update(keys=keys.tolist(), rect=keys[0, 14:18].tolist(),
                      group_start=column == 0, group_end=8 if column == 11 else -1,
                      stepped_rect=bool(np.any(keys[:, 14:18] != keys[0, 14:18])))
    merged.append(definition)
    checks.append(dict(column=column, period=period, parent_gating=True, selected_keys_preserved=True))
backup = p.with_name('armor.before_track_compaction.json')
assert not backup.exists()
backup.write_bytes(p.read_bytes())
renderer['layers'] = layers[:52] + merged + layers[100:]
data['source']['track_compaction'] = dict(before=114, after=78, exclusive_parent_period=135, checks=checks)
p.write_text(json.dumps(data, indent=1))
(p.parent / 'track_compaction_validation.json').write_text(json.dumps(dict(status='passed', **data['source']['track_compaction']), indent=2))
print('Proved parent gating and preserved all clocks: 114 -> 78 layers')
