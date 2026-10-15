"""Rebuild published native replacements under the client memory budget and prove they still draw the same.

  python rebuild_memory_budget.py plan --size 250       # group the live bundles into native60.. rebuild batches
  python rebuild_memory_budget.py build --batch native60   # inputs -> Unity -> gates -> equivalence proof
  python native_batches.py prepare|stage|apply|verify --batch native60   # the normal guarded publication

Every bundle published before 2026-10-14 kept a CPU-readable copy of its atlases, and three had 8192 px
atlases. A rebuild regenerates the Unity input from the retained conversion, requires it to equal the input
of the published build, and builds it with the current ArmorBuilder (non-readable atlases, texture budget
gate). Unless the conversion itself was replaced (SHRUNK: halved flipbook images, see shrink_atlas.py), the
rebuilt bundle must serialize exactly the same objects and texture data as the live bundle apart from
m_IsReadable (bundle_equivalence.py). Morph bundles also repeat their activated-pose GPU comparison.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess

import native_batches as n

WORK = n.BASE / 'memory_rebuild_20261014'
# A copy of n.PROJECT's settings, packages and scripts on the local SSD (outputs now live on an HDD).
# bundle_equivalence proves it builds the same bundles.
PROJECT = Path(os.environ['LOCALAPPDATA']) / 'swf2unity' / 'MemoryRebuildProject'
FIRST_BATCH = 60
SKIN_TEMPLATE = n.ROOT / 'outputs/flash_converter_trial/skin_fix/template'
REFERENCE_BACKUPS = (n.ROOT / 'outputs/flash_converter_trial/redux_inventory/generated_input_backups',
                     n.ROOT / 'outputs/battlepet_forge_lab/live/unity-input-backups')


def live_publications():
    """Latest verified publication per native bundle, with the build entry that produced it."""
    live = {}
    for payload in sorted(n.BASE.glob('native[0-9][0-9]/deployment_payload.json')):
        batch = payload.parent
        proof = batch / 'live_verification.json'
        if not proof.is_file() or json.loads(proof.read_text())['status'] != 'passed':
            continue
        plan = {e['native_bundle_id']: e for e in json.loads((batch / 'build_plan.json').read_text())}
        for e in json.loads(payload.read_text())['entries']:
            bid = e['bundle_id']
            if bid not in live or e['version'] >= live[bid]['version']:
                live[bid] = dict(version=e['version'], sha256=e['sha256'], batch=batch.name, entry=plan[bid])
    return live


# ArmorBuilder's values for fields that older build_armor.py versions did not write.
FIELD_DEFAULTS = {'morphFrame': -1, 'groupEnd': -1}


def same_input(generated, reference):
    """Equal, treating a field missing from an older input as ArmorBuilder's JsonUtility default."""
    if isinstance(generated, dict) and isinstance(reference, dict):
        for key in set(generated) | set(reference):
            if key in generated and key in reference:
                if not same_input(generated[key], reference[key]):
                    return False
            elif key in reference:
                return False
            elif key in FIELD_DEFAULTS:
                if generated[key] != FIELD_DEFAULTS[key]:
                    return False
            elif generated[key]:  # JsonUtility leaves other absent fields at false / 0 / empty
                return False
        return True
    if isinstance(generated, list) and isinstance(reference, list):
        return len(generated) == len(reference) and all(same_input(a, b) for a, b in zip(generated, reference))
    return generated == reference


def norm(path):
    return Path(path).as_posix().lower().rstrip('/')


def reference_input(entry):
    """The Unity input the published bundle was built from, matched by its output folder."""
    out = norm(Path(entry['bundle']).parent)
    paths = [p for folder in REFERENCE_BACKUPS for p in folder.glob(entry['key'] + '*.json')]
    if entry.get('unity_input'):
        paths += [Path(entry['unity_input']), Path(entry['unity_input']).with_name('recovery_input.json')]
    paths += [Path(entry['bundle']).parent.parent / name for name in ('unity_input.json', 'recovery_input.json')]
    found = {}
    for path in paths:
        if path.is_file():
            data = json.loads(path.read_text(encoding='utf-8'))
            if isinstance(data, dict) and norm(data.get('outputDir', '')) == out:
                found.setdefault(json.dumps({k: v for k, v in data.items() if k != 'validateFrames'}, sort_keys=True), str(path))
    assert len(found) <= 1, 'Published build has conflicting input records'
    return next(iter(found.values()), None)


def plan(size, pilot, last):
    """native60 holds the pilot bundles, then batches of `size`; bundles needing new conversions go last."""
    WORK.mkdir(exist_ok=True)
    assert not (WORK / 'plan.json').exists(), 'Rebuild plan already exists'
    rows = {}
    for bid, live in sorted(live_publications().items()):
        old = live['entry']
        assert old['bundle_sha256'] == live['sha256'] and Path(old['bundle']).is_file(), bid
        assert 'caladbolg' not in (old['name'] + ' ' + old['original'].get('File', '')).lower()
        rows[bid] = dict(bundle_id=bid, previous_batch=live['batch'], previous_version=live['version'],
                         previous_sha256=live['sha256'], previous_bundle=old['bundle'],
                         reference_input=reference_input(old), kind=old['kind'], name=old['name'])
    assert set(pilot) <= set(rows) and set(last) <= set(rows) and not set(pilot) & set(last)
    batches = {f'native{FIRST_BATCH:02}': [rows[bid] for bid in pilot]}
    middle = [row for bid, row in rows.items() if bid not in pilot and bid not in last]
    for index, row in enumerate(middle):
        batches.setdefault(f'native{FIRST_BATCH + 1 + index // size:02}', []).append(row)
    batches[f'native{FIRST_BATCH + 2 + (len(middle) - 1) // size:02}'] = [rows[bid] for bid in last]
    for name in batches:
        assert not (n.BASE / name).exists(), name + ' already exists'
    n.write(WORK / 'plan.json', dict(size=size, bundles=len(rows), batches=batches))
    print(json.dumps({name: len(v) for name, v in batches.items()}))


def build(batch_name):
    batch = n.BASE / batch_name
    rows = json.loads((WORK / 'plan.json').read_text())['batches'][batch_name]
    assert not (batch / 'deployment_result.json').exists()
    batch.mkdir(exist_ok=True)
    entries = json.loads((batch / 'prepared_plan.json').read_text()) if (batch / 'prepared_plan.json').exists() else []
    holds = json.loads((batch / 'held_candidates.json').read_text()) if (batch / 'held_candidates.json').exists() else []
    done = {e['native_bundle_id'] for e in entries} | {h['bundle_id'] for h in holds}
    # Bundles whose conversion was replaced to meet the budget (shrink_atlas.py, reduced color maps).
    replaced = json.loads((WORK / 'replaced_conversions.json').read_text()) if (WORK / 'replaced_conversions.json').exists() else {}
    for row in rows:
        row = dict(row, shrunk_conversion=replaced.get(str(row['bundle_id'])))
        bid = row['bundle_id']
        if bid in done:
            continue
        old = {e['native_bundle_id']: e for e in json.loads((n.BASE / row['previous_batch'] / 'build_plan.json').read_text())}[bid]
        entry = {k: v for k, v in old.items() if k not in ('bundle_sha256', 'unity_input', 'conversion', 'bundle', 'frames')}
        folder = batch / old['key']
        conversion = Path(row['shrunk_conversion'] or old['conversion'])
        data = json.loads((conversion / 'armor.json').read_text())
        frames = old.get('frames') or json.loads((conversion / 'validation.json').read_text()).get('samples')
        assert frames, 'No validation frames recorded'
        if row['shrunk_conversion']:
            frames = sorted(set(frames) | {0})
        template = SKIN_TEMPLATE if any(not gd.get('nodes') for gd in data['genders'].values()) else '-'
        entry.update(conversion=str(conversion), bundle=str(folder / 'bundle' / old['bundle_name']), frames=frames,
                     unity_input=str(PROJECT / 'Assets/Armors' / data['name'] / 'unity_input.json'),
                     memory_rebuild=dict(row, mode='shrunk' if row['shrunk_conversion'] else 'readable_off'))
        try:
            folder.mkdir(exist_ok=True)
            from batch_trials import TEXTURE_BUDGET_WAIVERS
            waiver = ['--texture-budget-bytes', TEXTURE_BUDGET_WAIVERS[bid]] if bid in TEXTURE_BUDGET_WAIVERS else []
            n.run('build_armor.py', folder, conversion, template, '--bundle', old['bundle_name'], '--project', PROJECT,
                  '--output-dir', folder / 'bundle', '--validate', ','.join(map(str, frames)), '--prepare-only', *waiver)
            if row['reference_input'] and not row['shrunk_conversion']:
                generated = json.loads(Path(entry['unity_input']).read_text())
                reference = json.loads(Path(row['reference_input']).read_text(encoding='utf-8'))
                strip = lambda d: {k: v for k, v in d.items() if k not in ('outputDir', 'validateFrames')}
                assert same_input(strip(generated), strip(reference)), 'Regenerated Unity input differs from the published build input'
            entries.append(entry)
            n.write(batch / 'prepared_plan.json', entries)
        except Exception as error:
            holds.append(dict(source_id=old['source_id'], bundle_id=bid, name=old['name'], reason='input: ' + str(error)[-600:]))
            n.write(batch / 'held_candidates.json', holds)
            print('INPUT_HELD', bid, str(error)[-200:], flush=True)
    assert entries, 'No rebuild inputs prepared'
    if not (batch / 'unity_batch.log').exists() or not (batch / 'build_summary.json').exists():
        import build_armor
        build_armor.sync_project(str(PROJECT))
        job = batch / 'unity_batch.json'
        n.write(job, dict(inputs=[e['unity_input'] for e in entries]))
        startup = subprocess.STARTUPINFO(); startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW; startup.wShowWindow = subprocess.SW_HIDE
        print('UNITY_BATCH_START', batch_name, len(entries), flush=True)
        result = subprocess.run([build_armor.UNITY, '-batchmode', '-quit', '-projectPath', str(PROJECT),
            '-executeMethod', 'ArmorBuilder.BuildMany', '-armorBatchInput', str(job), '-logFile', str(batch / 'unity_batch.log')],
            startupinfo=startup, creationflags=subprocess.CREATE_NO_WINDOW, timeout=4 * 3600, env=build_armor.unity_environment())
        print('UNITY_BATCH_EXIT', result.returncode, flush=True)
        n.finalize(batch, entries, holds, len(entries))
    prove(batch)


def prove(batch):
    """Equivalence with the live bundle (or activated-pose GPU checks for replaced conversions)."""
    from bundle_equivalence import compare
    from validate_morph import compare_gpu
    # finalize() writes build_plan.json; keep its full output so a re-run can re-prove held entries.
    if not (batch / 'finalized_plan.json').exists():
        shutil.copy2(batch / 'build_plan.json', batch / 'finalized_plan.json')
    built = json.loads((batch / 'finalized_plan.json').read_text())
    holds = [h for h in json.loads((batch / 'held_candidates.json').read_text()) if not h['reason'].startswith('proof: ')]
    proofs = json.loads((batch / 'memory_proofs.json').read_text()) if (batch / 'memory_proofs.json').exists() else {}
    accepted = []
    for entry in built:
        bid = str(entry['native_bundle_id'])
        try:
            if bid not in proofs or proofs[bid].get('sha256') != entry['bundle_sha256']:
                proof = dict(sha256=entry['bundle_sha256'], mode=entry['memory_rebuild']['mode'])
                model = json.loads((Path(entry['conversion']) / 'armor.json').read_text())
                if any(r.get('morph') for gd in model['genders'].values() for r in gd['renderers'].values()):
                    compare_gpu(Path(entry['conversion']), Path(entry['bundle']).parent / 'unity_validation', frames=entry['frames'])
                    proof['morph_gpu'] = 'passed'
                if proof['mode'] == 'readable_off':
                    proof['equivalence'] = compare(entry['memory_rebuild']['previous_bundle'], entry['bundle'])
                    assert proof['equivalence']['status'] in ('identical', 'identical_except_builder_upgrade'), proof['equivalence']
                contract = json.loads((Path(entry['bundle']).parent / 'bundle_contract.json').read_text())
                proof['texture_bytes'] = contract['textureBytes']
                proofs[bid] = proof
                n.write(batch / 'memory_proofs.json', proofs)
            accepted.append(entry)
        except Exception as error:
            holds = [h for h in holds if h['bundle_id'] != entry['native_bundle_id']]
            holds.append(dict(source_id=entry['source_id'], bundle_id=entry['native_bundle_id'], name=entry['name'],
                              reason='proof: ' + str(error)[-600:]))
            print('PROOF_HELD', bid, str(error)[-300:], flush=True)
    n.write(batch / 'held_candidates.json', holds)
    n.write(batch / 'build_plan.json', accepted)
    print('MEMORY_PROVED', batch.name, len(accepted), 'held', len(holds), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('plan', 'build', 'prove'))
    parser.add_argument('--batch')
    parser.add_argument('--size', type=int, default=250)
    parser.add_argument('--pilot', help='Comma-separated bundle ids for the first batch')
    parser.add_argument('--last', help='Comma-separated bundle ids that need replaced conversions')
    args = parser.parse_args()
    if args.mode == 'plan':
        assert 1 <= args.size <= 256
        ids = lambda text: [int(v) for v in text.split(',')] if text else []
        plan(args.size, ids(args.pilot), ids(args.last))
    else:
        assert args.batch and json.loads((WORK / 'plan.json').read_text())['batches'].get(args.batch) is not None
        (build if args.mode == 'build' else lambda name: prove(n.BASE / name))(args.batch)


if __name__ == '__main__':
    main()
