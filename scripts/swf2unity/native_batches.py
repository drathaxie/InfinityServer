"""Build increasing batches and replace real native bundle IDs/files without creating trial items."""
import argparse
import csv
from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
import re
import subprocess
import sys
import tarfile

from trial_host import ROOT, remote, server_script

BASE = ROOT / 'outputs/flash_converter_trial/native_replacements'
PROJECT = ROOT / 'outputs/flash_converter_trial/FlyingMoglin/UnityProject'
SCRIPTS = ROOT / 'scripts/swf2unity'


def write(path, value):
    path.write_text(json.dumps(value, indent=2))


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def refresh_summary():
    batches, pending, roster = [], [], []
    for directory in sorted(BASE.glob('native[0-9][0-9]')):
        verification = directory / 'live_verification.json'
        if verification.exists():
            verified = json.loads(verification.read_text())
            result = json.loads((directory / 'deployment_result.json').read_text())
            entries = verified['entries']
            payload = json.loads((directory / 'deployment_payload.json').read_text())
            before = {e['bundle_id']: e for e in payload['before']['entries']}
            published = {e['bundle_id']: e for e in payload['entries']}
            for entry in entries:
                replacement = published[entry['bundle_id']]
                for item in before[entry['bundle_id']]['items']:
                    roster.append(dict(batch=directory.name, item_id=item['id'], name=item['Name'],
                        bundle_id=entry['bundle_id'], version=entry['version'], filename=entry['filename'],
                        prefab=item['prefab_name'], flash_source=item['File']))
            batches.append(dict(batch=directory.name, bundles=len(entries),
                item_records=sum(len(e['original_item_ids']) for e in entries),
                bytes=sum(e['bytes'] for e in entries), backup=result['backup'], status=verified['status']))
        elif (directory / 'prepared_plan.json').exists():
            pending.append(dict(batch=directory.name, prepared=len(json.loads((directory / 'prepared_plan.json').read_text())),
                published=(directory / 'deployment_result.json').exists()))
    roster = list({row['item_id']: row for row in roster}.values())
    summary = dict(published_batches=batches, total_bundles=len({row['bundle_id'] for row in roster}),
        total_item_records=len(roster),
        largest_batch=max((b['bundles'] for b in batches), default=0), pending=pending,
        gif_url='https://content.130-162-189-229.sslip.io/previews/gigante-force-spear-6abcae431876.gif')
    write(BASE / 'summary.json', summary)
    with (BASE / 'published_items.csv').open('w', newline='', encoding='utf-8-sig') as handle:
        writer = csv.DictWriter(handle, fieldnames=('batch', 'item_id', 'name', 'bundle_id', 'version', 'filename', 'prefab', 'flash_source'))
        writer.writeheader()
        writer.writerows(roster)
    rows = ['| Batch | Native bundles | Existing item records | Bytes |', '|---|---:|---:|---:|']
    rows += [f"| {b['batch']} | {b['bundles']} | {b['item_records']} | {b['bytes']:,} |" for b in batches]
    text = '# Native Flash-to-Unity asset replacements\n\n'
    text += f"Verified live: **{summary['total_bundles']} native bundles**, covering **{summary['total_item_records']} existing item records**. Largest published batch: **{summary['largest_batch']} bundles**.\n\n"
    text += '\n'.join(rows) + '\n\n'
    text += 'These are replacements on the original bundle IDs, item IDs, prefab names, and logical Unity filenames. Each publication increments the published version and CRC; previous version files and metadata remain rollback points. No new trial items are created. A separate idempotent grant phase adds missing published items to Redux without changing existing inventory rows or equipment. All non-bundle item fields and unrelated catalog entries are guarded and preserved. Caladbolg is excluded.\n\n'
    text += 'Every published bundle passed Flash-source comparison, loaded-bundle Animator/render checks, and an independent serialized AssetBundle audit. Additional sword batches hold sources with color-custom scripts, procedural animation, unsupported masks/filters, long capped clocks, or excessive flipbook workloads. Numeric source limits remain mean <= 25/255 and clipped alpha <= 0.5; GPU limits remain mean <= 2/255 and p99 <= 24/255. The spear retains its separately documented color reconstruction and three static glow approximations.\n\n'
    late_path = BASE / 'late_boundary_audit.json'
    if late_path.exists():
        late = json.loads(late_path.read_text())
        text += f"Supplemental source checks for {len(late['checks'])} earlier animated swords: **{late['status']}**. `late_boundary_audit.json` records source comparisons at each individual animation period boundary and the second loop boundary. From native04 onward, these late source frames are part of the build gate.\n\n"
    if (BASE / 'native05/live_verification.json').exists():
        text += 'Native05 updates 15 earlier flipbook bundles (17 existing item records) with the short-loop closing-step guard. The old serialized J6 Cutlass reproduced a wrong pose at frame 10; the corrected bundle passed at that frame and around its fifth loop. The guard moves only the closing step slightly early while retaining the exact clip duration; existing armor curves are unchanged. An intentional validation exception followed by successful captures proves that one failed item no longer contaminates later comparisons. These updates are counted once in the live totals and item roster.\n\n'
    text += 'The native version serializer now caches the registry by its file revision, so the running game can see subsequent asset version updates. The fix passed a regression test and was activated with service and TCP listener checks. Two early startup checks rolled back before activation; logs established that game seeding takes about 14 seconds, and the successful activation allowed 40 seconds for readiness. Both API and game services were healthy afterward. Asset publications after activation preserve the game PID.\n\n'
    text += 'Publication manifests, raw before snapshots, backups, independent audits, and public hash/API/item-wire readbacks are under `outputs/flash_converter_trial/native_replacements/nativeXX/`. Each `deployment_result.json` records its server rollback directory. `summary.json` separates verified publication from unfinished preparation.\n\n'
    text += 'Backup catalogs are historical snapshots. After later batches have published, a rollback must restore only the affected IDs with current-state guards.\n\n'
    text += '[Published item roster](../outputs/flash_converter_trial/native_replacements/published_items.csv) lists the original item IDs, names, and current bundle versions for testing.\n\n'
    text += 'Real player equip, hand alignment, visual appearance, and combat behavior remain unverified for the bulk replacements. Restart the client to fetch the new versions. Native spear bundle 71508 is version 2 and serves both original items 98957 and 99045; its previous trial item remains available separately.\n\n'
    text += '[Mobile spear GIF](' + summary['gif_url'] + ')\n\n'
    text += 'Backups:\n\n' + '\n'.join('- `' + b['backup'] + '`' for b in batches) + '\n'
    (ROOT / 'docs/FLASH_NATIVE_ASSET_REPLACEMENTS.md').write_text(text, encoding='utf-8')
    return summary


def run(script, folder, *args):
    import os
    log = folder / (script.removesuffix('.py') + '.log')
    environment = dict(os.environ, PYTHONIOENCODING='utf-8')
    with log.open('w', encoding='utf-8') as output:
        result = subprocess.run([sys.executable, '-u', str(SCRIPTS / script), *map(str, args)], cwd=ROOT,
                                stdout=output, stderr=subprocess.STDOUT, env=environment)
    if result.returncode:
        raise RuntimeError(log.read_text(encoding='utf-8', errors='replace')[-3600:])


def build(batch, size, universal=False, source_only=False):
    import rig
    import swfparse
    import ccscripts
    import build_armor
    from batch_trials import audit
    assert not (batch / 'deployment_result.json').exists()
    candidates = json.loads((BASE / 'discovery.json').read_text())['candidates']
    manifest = json.loads((BASE / 'source_manifest.json').read_text())
    used = set()
    for path in BASE.glob('native*/build_plan.json'):
        if path.parent != batch:
            used.update(e['native_bundle_id'] for e in json.loads(path.read_text()))
    for path in BASE.glob('native*/prepared_plan.json'):
        if path.parent != batch:
            used.update(e['native_bundle_id'] for e in json.loads(path.read_text()))
    if not universal:
        for path in BASE.glob('native*/held_candidates.json'):
            if path.parent != batch:
                used.update(e['bundle_id'] for e in json.loads(path.read_text()))
    else:
        failed_ids = {e['bundle_id'] for path in BASE.glob('native*/held_candidates.json')
                      for e in json.loads(path.read_text())}
        candidates.sort(key=lambda e: e['template']['Bundle']['ID'] not in failed_ids)
    entries = json.loads((batch / 'prepared_plan.json').read_text()) if (batch / 'prepared_plan.json').exists() else []
    holds = json.loads((batch / 'held_candidates.json').read_text()) if (batch / 'held_candidates.json').exists() else []
    used.update(e['native_bundle_id'] for e in entries)
    used.update(e['bundle_id'] for e in holds)
    for candidate in candidates:
        if len(entries) >= size:
            break
        original, template = candidate['original'], candidate['template']
        bid = template['Bundle']['ID']
        if bid in used:
            continue
        used.add(bid)
        key = 'NativeSword' + str(bid)
        folder = batch / key; folder.mkdir(exist_ok=True)
        source = BASE / 'sources' / original['File']
        conversion, bundle_folder = folder / 'conversion', folder / 'bundle'
        try:
            assert sha(source) == manifest[original['File']]['sha256']
            movie = swfparse.load(str(source))
            cid = next(c for c, n in movie.symbols.items() if n == original['Link'])
            cc = ccscripts.cc_chars(movie, str(source), str(folder / 'source_scripts'))
            assert universal or not cc, 'Color-custom source held for dedicated palette validation: ' + repr(sorted(set(cc.values())))
            from source_script_audit import audit as audit_scripts
            audit_scripts(source, folder / 'source_scripts', allow_initial_stops=universal)
            if universal:
                from timeline_scripts import lower_initial_stops
                programs = lower_initial_stops(movie, folder / 'source_scripts')
                write(folder / 'timeline_programs.json', programs)
            rig.MAX_PERIOD = 10000 if universal else 960
            source_rig = rig.Rig(movie, expanded=universal, cc=cc, faithful_effects=universal)
            layers = source_rig.decompose(cid)
            write(folder / 'source_screen.json', dict(layers=[dict(kind=l.kind, period=l.period) for l in layers], notes=source_rig.notes))
            assert not source_rig.notes and len(layers) <= (128 if universal else 32), 'Unsupported rig: ' + repr(source_rig.notes)
            workload = sum(l.period for l in layers if l.kind == 'flipbook')
            assert universal or workload <= 256, 'Held for dedicated render pass: ' + str(workload) + ' flipbook images'
            assert template.get('PrefabName'), 'Native prefab name missing'
            source_frames = max([l.period for l in layers] or [1])
            source_samples = sorted(set(range(min(source_frames, 36))) |
                {f for layer in layers if layer.period > 1 for f in (layer.period - 1, layer.period,
                    2 * layer.period - 1, 2 * layer.period, 5 * layer.period - 1, 5 * layer.period, 5 * layer.period + 1)})
            filename = Path(template['Bundle'].get('Filename') or template['Bundle'].get('FileName')).name
            assert filename.endswith('.unity3d')
            print('CONVERT', bid, original['Name'], 'period', source_frames, flush=True)
            run('weapon.py', folder, source, original['Link'], conversion, '--prefab', template['PrefabName'],
                '--name', key, '--validate-samples', ','.join(map(str, source_samples)),
                *(['--expanded', '--faithful-effects', '--max-period', '10000', '--max-layers', '128',
                   *(['--rgb-cc'] if cc else [])] if universal else []))
            data = json.loads((conversion / 'armor.json').read_text())
            assert not data.get('notes'), data.get('notes')
            source_check = json.loads((conversion / 'validation.json').read_text())
            assert source_check['worst_mean'] <= 25 and source_check['max_clipped_alpha'] <= 0.5, source_check
            periods = sorted({l['period'] for gd in data['genders'].values() for r in gd['renderers'].values() for l in r.get('layers') or []})
            frames = sorted({0, *[f for p in periods if p > 1 for f in (1, p - 1, p, 2 * p - 1, 2 * p,
                5 * p - 1, 5 * p, 5 * p + 1)]})
            run('build_armor.py', folder, conversion, '-', '--bundle', filename, '--project', PROJECT,
                '--output-dir', bundle_folder, '--validate', ','.join(map(str, frames)), '--prepare-only')
            entry = dict(source_id=original['id'], native_bundle_id=bid, key=key, kind='weapon', name=original['Name'],
                prefab=template['PrefabName'], original=original, sources=[dict(relative=original['File'], path=str(source), **manifest[original['File']])],
                conversion=str(conversion), bundle=str(bundle_folder / filename), bundle_name=filename, frames=frames,
                unity_input=str(PROJECT / 'Assets/Armors' / key / 'unity_input.json'))
            entries.append(entry)
            write(batch / 'prepared_plan.json', entries)
        except (Exception, SystemExit) as error:
            holds.append(dict(source_id=original['id'], bundle_id=bid, name=original['Name'], reason=str(error) or type(error).__name__))
            write(batch / 'held_candidates.json', holds)
            print('HELD', bid, str(error)[:240], flush=True)
        if len(entries) >= size:
            break
    assert entries, 'No compatible native sources remain in the captured set'
    build_armor.sync_project(str(PROJECT))
    job = batch / 'unity_batch.json'; write(job, dict(inputs=[e['unity_input'] for e in entries]))
    if source_only:
        print('SOURCE_BATCH_PREPARED', batch.name, len(entries), flush=True)
        return
    log = batch / 'unity_batch.log'
    startup = subprocess.STARTUPINFO(); startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW; startup.wShowWindow = subprocess.SW_HIDE
    print('UNITY_BATCH_START', batch.name, len(entries), flush=True)
    result = subprocess.run([build_armor.UNITY, '-batchmode', '-quit', '-projectPath', str(PROJECT),
        '-executeMethod', 'ArmorBuilder.BuildMany', '-armorBatchInput', str(job), '-logFile', str(log)],
        startupinfo=startup, creationflags=subprocess.CREATE_NO_WINDOW, timeout=7200, env=build_armor.unity_environment())
    print('UNITY_BATCH_EXIT', result.returncode, flush=True)
    finalize(batch, entries, holds, size)


def finalize(batch, entries, holds, size, built=None, prepared_count=None):
    import build_armor
    from batch_trials import audit
    import numpy as np
    from PIL import Image
    import validate_armor as va
    built = list(built or [])
    for entry in entries:
        try:
            output = Path(entry['bundle']).parent
            assert not (output / 'build_failure.txt').exists(), (output / 'build_failure.txt').read_text()
            contract = json.loads((output / 'bundle_contract.json').read_text())
            data = json.loads((Path(entry['conversion']) / 'armor.json').read_text())
            sims, recon = {}, {}
            for gender, gd in data['genders'].items():
                sims[gender] = np.asarray(Image.open(Path(entry['conversion']) / gd['atlas']).convert('RGBA'), np.float32) / 255
                recon[gender] = va.load_reconstruction(entry['conversion'], gd)
                if gd['itemslots'] and not gd.get('direct_atlas'):
                    sims[gender] = va.composite((sims[gender] * 255 + .5).astype(np.uint8), entry['conversion'], gd)
            summary = build_armor.compare(data, sims, str(output), entry['frames'], reconstructions=recon)
            entry['bundle_sha256'] = sha(entry['bundle'])
            write(output / 'build_validation.json', dict(status='passed', bundle_sha256=entry['bundle_sha256'],
                bundle_crc=contract['bundleCRC'], unity_render_validation=summary, bundle_contract=contract))
            audit(entry)
            built.append(entry)
            write(batch / 'build_plan.json', built)
            print('AUDITED', entry['native_bundle_id'], entry['name'], flush=True)
        except Exception as error:
            holds.append(dict(source_id=entry['source_id'], bundle_id=entry['native_bundle_id'], name=entry['name'], reason=str(error)))
            write(batch / 'held_candidates.json', holds)
            print('BUILD_HELD', entry['native_bundle_id'], str(error)[:300], flush=True)
    assert built
    accepted_ids = {entry['native_bundle_id'] for entry in built}
    holds = [entry for entry in holds if entry['bundle_id'] not in accepted_ids]
    write(batch / 'held_candidates.json', holds)
    write(batch / 'build_summary.json', dict(requested=size,
        prepared=len(entries) if prepared_count is None else prepared_count, passed=len(built), held=len(holds)))
    print('BATCH_BUILT', batch.name, len(built), 'held', len(holds), flush=True)


def seed_spear(batch):
    entry = json.loads((ROOT / 'outputs/flash_converter_trial/batch05/build_plan.json').read_text())[0]
    entry['name'] = 'Gigante Force Spear'
    write(batch / 'build_plan.json', [entry])


def prepare(batch):
    from batch_trials import audit
    from source_script_audit import audit as audit_scripts
    plan = json.loads((batch / 'build_plan.json').read_text())
    accepted = []
    holds = json.loads((batch / 'held_candidates.json').read_text()) if (batch / 'held_candidates.json').exists() else []
    for entry in plan:
        try:
            script_folder = Path(entry['conversion']).parent / 'source_scripts'
            if script_folder.exists():
                converted = json.loads((Path(entry['conversion']) / 'armor.json').read_text())
                audit_scripts(entry['sources'][0]['path'], script_folder,
                              allow_initial_stops=bool(converted.get('source', {}).get('faithful_effects')),
                              allow_random_initial=bool(converted.get('source', {}).get('interactive_morph')),
                              allow_interactive=bool(converted.get('source', {}).get('interactive_morph')))
            audit(entry)
            accepted.append(entry)
        except Exception as error:
            holds.append(dict(source_id=entry['source_id'], bundle_id=entry['native_bundle_id'], name=entry['name'], reason=str(error)))
            print('PREFLIGHT_HELD', entry['native_bundle_id'], str(error)[:200], flush=True)
    assert accepted, 'No audited builds remain for native publication'
    if len(accepted) != len(plan):
        assert not (batch / 'deployment_result.json').exists()
        write(batch / 'held_candidates.json', holds)
        write(batch / 'build_plan.json', accepted)
    plan = accepted
    ids = [e['native_bundle_id'] for e in plan]
    script = (SCRIPTS / 'deploy_native_batch.py').read_bytes()
    screening=json.loads(server_script('-', '--screen ' + ' '.join(map(str,ids)),data=script))
    write(batch/'catalog_compatibility_screen.json',screening)
    if screening['rejected']:
        reasons={e['bundle_id']:e['reason'] for e in screening['rejected']}
        for source in plan:
            if source['native_bundle_id'] in reasons:
                holds.append(dict(source_id=source['source_id'],bundle_id=source['native_bundle_id'],name=source['name'],reason='Native catalog compatibility: '+reasons[source['native_bundle_id']]))
        plan=[e for e in plan if e['native_bundle_id'] in screening['accepted_ids']]
        assert plan,'No catalog-compatible bundles remain'
        write(batch/'held_candidates.json',holds);write(batch/'build_plan.json',plan)
        summary=json.loads((batch/'build_summary.json').read_text())
        summary.update(publication_accepted=len(plan),publication_rejected=len(screening['rejected']));write(batch/'build_summary.json',summary)
        ids=[e['native_bundle_id'] for e in plan]
    before = json.loads(server_script('-', '--inspect ' + ' '.join(map(str, ids)), data=script))
    write(batch / 'live_preflight.json', before)
    by_id = {e['bundle_id']: e for e in before['entries']}
    entries = []
    for source in plan:
        bid = source['native_bundle_id']; old = by_id[bid]
        if source.get('original'):
            owner = next(i for i in old['items'] if i['id'] == source['source_id'])
            assert all(owner.get(k) == source['original'].get(k) for k in ('File','Link','Type','prefab_name')), \
                f'Bundle {bid} source owner changed art/linkage/type/prefab since capture'
        assert all(i['prefab_name'] and i['prefab_name'].casefold() == source['prefab'].casefold() for i in old['items'])
        bundle = deepcopy(old['bundle'])
        version = max([int(bundle.get(k) or 0) for k in ('Version', 'VersionContent', 'VersionStage', 'VersionLive', '_cdnvL')]
            + [int(json.loads(i['bundle']).get(k) or 0) for i in old['items'] for k in ('Version', 'VersionContent', 'VersionStage', 'VersionLive')]) + 1
        contract = json.loads((Path(source['bundle']).parent / 'bundle_contract.json').read_text())
        bundle.update({k: version for k in ('Version', 'VersionContent', 'VersionStage', 'VersionLive', '_cdnvL')})
        bundle['CRCPC'] = str(contract['bundleCRC'])
        filename = Path(bundle.get('FileName') or bundle.get('Filename'))
        target = Path('/opt/infinity/content/game/assetbundles/windows') / filename.parent / filename.stem / str(version) / filename.name
        entries.append(dict(bundle_id=bid, version=version, bundle=bundle, target=target.as_posix(),
            sha256=source['bundle_sha256'], crc=contract['bundleCRC'], prefab=source['prefab'],
            sources=[{k: s[k] for k in ('relative', 'sha256', 'bytes')} for s in source['sources']]))
    payload=dict(batch=batch.name, before=before, entries=entries)
    if any(e['kind']=='helm' for e in plan):
        assert all(json.loads((Path(e['bundle']).parent/'bundle_contract.json').read_text()).get('helmAttachmentContract')==2 for e in plan if e['kind']=='helm')
        payload['helm_attachment_contract']=2
    write(batch / 'deployment_payload.json', payload)
    print('PREPARED', batch.name, len(entries), 'native bundles', sum(len(e['items']) for e in before['entries']), 'original items')


def stage(batch):
    plan = json.loads((batch / 'build_plan.json').read_text())
    payload = json.loads((batch / 'deployment_payload.json').read_text())
    files = {Path(e['target']).name: Path(source['bundle']) for e, source in zip(payload['entries'], plan)}
    assert len(files) == len(plan)
    files.update({'payload.json': batch / 'deployment_payload.json', 'deploy_native_batch.py': SCRIPTS / 'deploy_native_batch.py',
        'deploy_pet_trial.py': SCRIPTS / 'deploy_pet_trial.py'})
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode='w') as archive:
        for name, path in files.items():
            archive.add(path, arcname=name)
    destination = '/tmp/infinity-' + batch.name + '-20261012'
    remote('mkdir -p ' + destination + ' && tar -xf - -C ' + destination, stream.getvalue())
    result = json.loads(server_script(destination + '/deploy_native_batch.py', '--payload ' + destination + '/payload.json'))
    assert result['status'] == 'preflight_passed'
    write(batch / 'deployment_dry_run.json', result); print(json.dumps(result))


def deploy(batch, verify=False):
    destination = '/tmp/infinity-' + batch.name + '-20261012'
    result_path = batch / ('live_verification.json' if verify else 'deployment_result.json')
    if not verify:
        assert not result_path.exists() and json.loads((batch / 'deployment_dry_run.json').read_text())['status'] == 'preflight_passed'
    result = json.loads(server_script(destination + '/deploy_native_batch.py', '--payload ' + destination + '/payload.json ' + ('--verify' if verify else '--apply')))
    write(result_path, result)
    if verify:
        refresh_summary()
        print(json.dumps(dict(status=result['status'], batch=batch.name, bundles=len(result['entries']),
            original_item_records=sum(len(e['original_item_ids']) for e in result['entries']), services=result['services'], proof_boundary=result['proof_boundary'])))
    else:
        print(json.dumps({k: v for k, v in result.items() if k != 'bundles'} | dict(batch=batch.name, bundles=len(result['bundles']))))


def pipeline(batch, size, universal=False):
    status_path = batch / 'pipeline_status.json'
    for phase in ('build', 'prepare', 'stage', 'apply', 'verify', 'grant'):
        if phase not in ('verify', 'grant') and (batch / 'deployment_result.json').exists():
            continue
        if phase == 'build' and (batch / 'build_summary.json').exists():
            continue
        write(status_path, dict(status='running', phase=phase, requested=size))
        try:
            if phase == 'build': build(batch, size, universal)
            elif phase == 'prepare': prepare(batch)
            elif phase == 'stage': stage(batch)
            elif phase == 'grant':
                run('grant_converted_redux_host.py', batch, '--apply')
                selection=ROOT/'outputs/flash_converter_trial/redux_inventory/selection.json'
                if selection.exists() and json.loads(selection.read_text()).get('mode')=='redux_inventory_only':
                    run('audit_redux_conversions.py',batch)
                    lock=BASE/'specialty_worker/worker.lock'
                    if lock.exists():
                        run('prune_generated_native_inputs.py',batch,'--apply','--worker-pid',lock.read_text())
            else: deploy(batch, phase == 'verify')
        except Exception as error:
            write(status_path, dict(status='failed', phase=phase, requested=size, error=str(error)))
            raise
    write(status_path, dict(status='complete', phase='grant', requested=size))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('build', 'build-sources', 'seed-spear', 'prepare', 'stage', 'apply', 'verify', 'summary', 'run'))
    parser.add_argument('--batch', required=True)
    parser.add_argument('--size', type=int, default=32)
    parser.add_argument('--universal', action='store_true', help='Retry previous feature failures with RGB palettes, animated effects and expanded rigs')
    args = parser.parse_args()
    assert re.fullmatch(r'native\d{2}', args.batch) and 1 <= args.size <= 256
    batch = BASE / args.batch; batch.mkdir(exist_ok=True)
    if args.mode == 'summary': print(json.dumps(refresh_summary()))
    elif args.mode == 'build': build(batch, args.size, args.universal)
    elif args.mode == 'build-sources': build(batch, args.size, args.universal, source_only=True)
    elif args.mode == 'seed-spear': seed_spear(batch)
    elif args.mode == 'prepare': prepare(batch)
    elif args.mode == 'stage': stage(batch)
    elif args.mode == 'run': pipeline(batch, args.size, args.universal)
    else: deploy(batch, args.mode == 'verify')


if __name__ == '__main__':
    main()
