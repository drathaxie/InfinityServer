"""Discover and build varied native items, recording novel scripted source cases."""
import argparse
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tarfile

import native_batches as n
from trial_host import ROOT, remote, server_script

TYPES = ('Helm', 'Dagger', 'Mace', 'Staff', 'Bow', 'Gun', 'Gauntlet', 'Axe', 'Polearm',
         'Sword', 'Whip', 'Wand', 'Rifle', 'HandGun')


def discover(limit=360):
    destination = n.BASE / 'specialty_discovery.json'
    script = '''import db,json,pathlib,hashlib
c=db.connect();rows=[];source_cache={}
for kind in TYPES:
 query="SELECT * FROM items WHERE Type=? AND id<2000000 AND JSON_VALID(bundle) ORDER BY id DESC"+(' LIMIT '+str(LIMIT) if LIMIT else '')
 for row in c.execute(query,(kind,)).fetchall():
  original=dict(row);name=(original['Name']+' '+original['File']).lower()
  if 'caladbolg' in name:continue
  metadata=json.loads(original['bundle'])
  if not isinstance(metadata,dict) or not 0<int(metadata.get('ID') or 0)<2000000:continue
  source=pathlib.Path('/var/www/swordshine/gamefiles')/original['File']
  if source.suffix.lower()!='.swf' or not original['Link'] or not source.is_file():continue
  relative=original['File']
  if relative not in source_cache:source_cache[relative]=dict(bytes=source.stat().st_size,sha256=hashlib.sha256(source.read_bytes()).hexdigest())
  rows.append(dict(original=original,template=db.item(c,original['id']),source=source_cache[relative]))
print(json.dumps(rows,default=str));c.close()
'''
    rows = json.loads(server_script('-', data=('TYPES = ' + repr(TYPES) + '\nLIMIT = '+repr(limit)+'\n' + script).encode()))
    temporary = destination.with_suffix('.next.json')
    n.write(temporary, rows)
    temporary.replace(destination)
    print('SPECIALTY_DISCOVERED', len(rows), flush=True)
    print(json.dumps({kind: sum(r['original']['Type'] == kind for r in rows) for kind in TYPES}), flush=True)


def capture(rows):
    relatives = sorted({r['original']['File'] for r in rows})
    assert all(not Path(p).is_absolute() and '..' not in Path(p).parts and '\\' not in p for p in relatives)
    base = n.BASE / 'specialty_sources'
    if all((base/r['original']['File']).is_file() and n.sha(base/r['original']['File']) == r['source']['sha256'] for r in rows):
        return {r['original']['File']: base/r['original']['File'] for r in rows}
    script = '''import io,tarfile,sys,pathlib
root=pathlib.Path('/var/www/swordshine/gamefiles')
with tarfile.open(fileobj=sys.stdout.buffer,mode='w|') as archive:
 for relative in FILES:archive.add(root/relative,arcname=relative)
'''
    raw = server_script('-', data=('FILES = ' + repr(relatives) + '\n' + script).encode())
    sources = {}
    with tarfile.open(fileobj=io.BytesIO(raw), mode='r:') as archive:
        for row in rows:
            relative = row['original']['File']
            if relative in sources:
                continue
            data = archive.extractfile(relative).read()
            assert hashlib.sha256(data).hexdigest() == row['source']['sha256']
            path = base / relative
            assert path.resolve().is_relative_to(base.resolve())
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            sources[relative] = path
    return sources


def build(batch, size):
    import copy
    import ccscripts
    import rig
    import swfparse as sp
    import build_armor
    from source_script_audit import audit as audit_scripts
    from interactive_morph import Compiler
    inventory_selection=ROOT/'outputs/flash_converter_trial/redux_inventory/selection.json'
    selection=json.loads(inventory_selection.read_text()) if inventory_selection.exists() else {}
    source_file=Path(selection['specialty_file']) if selection.get('mode')=='redux_inventory_only' else n.BASE/'specialty_discovery.json'
    rows = json.loads(source_file.read_text())
    print('SPECIALTY_SCOPE',selection.get('mode','general_catalog'),len(rows),'source bundles',flush=True)
    used = {e['bundle_id'] for p in n.BASE.glob('native*/live_verification.json') for e in json.loads(p.read_text())['entries']}
    rows = [r for r in rows if 0 < r['template']['Bundle']['ID'] < 2000000 and r['original']['id'] < 2000000 and r['template']['Bundle']['ID'] not in used]
    by_type = {kind: [] for kind in TYPES}
    seen = set()
    for row in rows:
        bid = row['template']['Bundle']['ID']
        if bid in seen:
            continue
        seen.add(bid)
        by_type[row['original']['Type']].append(row)
    for values in by_type.values():
        values.sort(key=lambda r: (not any(word in r['original']['Name'].lower() for word in
            ('morph', 'shifting', 'random', 'transform', 'color custom', 'invisible', 'click')), -r['original']['id']))
    candidates = []
    for index in range(max(map(len, by_type.values()), default=0)):
        for kind in TYPES:
            if index < len(by_type[kind]):
                candidates.append(by_type[kind][index])
    candidates = candidates[:max(size * 4, 36)]
    if (batch/'candidate_plan.json').exists():
        candidates = json.loads((batch/'candidate_plan.json').read_text())
    else:
        n.write(batch / 'candidate_plan.json', candidates)
    sources = capture(candidates)
    entries = json.loads((batch/'prepared_plan.json').read_text()) if (batch/'prepared_plan.json').exists() else []
    held = json.loads((batch/'held_candidates.json').read_text()) if (batch/'held_candidates.json').exists() else []
    discoveries = json.loads((batch/'feature_discoveries.json').read_text()) if (batch/'feature_discoveries.json').exists() else []
    for entry in entries:
        if not Path(entry['unity_input']).exists():
            conversion = Path(entry['conversion'])
            check = json.loads((conversion/'validation.json').read_text())
            assert check['worst_mean'] <= 25 and check['max_clipped_alpha'] <= .5
            n.run('build_armor.py', conversion.parent, conversion, '-', '--bundle', entry['bundle_name'],
                  '--project', n.PROJECT, '--output-dir', Path(entry['bundle']).parent,
                  '--validate', ','.join(map(str, entry['frames'])), '--prepare-only')
    attempted = {e['native_bundle_id'] for e in entries} | {e['bundle_id'] for e in held}
    rig.MAX_PERIOD = 10000
    for row in candidates:
        if len(entries) >= size:
            break
        original, template = row['original'], row['template']
        bid = template['Bundle']['ID']
        if bid in attempted:
            continue
        folder = batch / ('Specialty' + str(bid)); folder.mkdir(exist_ok=True)
        source = sources[original['File']]
        conversion, output = folder / 'conversion', folder / 'bundle'
        try:
            movie = sp.load(str(source))
            cid = next((cid for cid, name in movie.symbols.items() if name == original['Link']), None)
            assert cid is not None, f"Linkage {original['Link']!r} is not exported; source exports {sorted(movie.symbols.values())[:12]}"
            cc = ccscripts.cc_chars(movie, str(source), str(folder / 'source_scripts'))
            interactive = False
            try:
                screen = audit_scripts(source, folder / 'source_scripts', allow_initial_stops=True, allow_random_initial=True)
                interactive = bool(screen['random_initial_selectors'])
            except AssertionError:
                interactive = True
            if interactive:
                compiler = Compiler(copy.deepcopy(movie), folder / 'source_scripts')
                controls = compiler.reachable_controls(cid)
                assert controls, 'Unsupported executable script has no compiled reachable selector'
                layers, program = compiler.decompose(rig.Rig(compiler.movie, expanded=True, cc=dict(cc), faithful_effects=True), cid)
                discoveries.append(dict(bundle_id=bid, name=original['Name'], type=original['Type'],
                                        behavior=program, controls=len(controls)))
            else:
                from timeline_scripts import lower_initial_stops
                lower_initial_stops(movie, folder / 'source_scripts')
                source_rig = rig.Rig(movie, expanded=True, cc=cc, faithful_effects=True)
                layers = source_rig.decompose(cid)
                assert not source_rig.notes, source_rig.notes
                discoveries.append(dict(bundle_id=bid, name=original['Name'], type=original['Type'],
                    colors=sorted({loc for loc, _ in cc.values()}), blend_modes=sorted({l.blend for l in layers if l.blend}),
                    periods=sorted({l.period for l in layers})))
            periods = sorted({l.period for l in layers})
            samples = sorted({0, *[f for p in periods if p > 1 for f in (1, p-1, p, 2*p-1, 2*p, 5*p-1, 5*p, 5*p+1)]})
            assert max(samples) < 65535
            name = 'Specialty' + str(bid)
            print('SPECIALTY_CONVERT', bid, original['Type'], original['Name'], 'interactive', interactive, flush=True)
            n.write(batch/'feature_discoveries.json',discoveries)
            converter = 'helm.py' if original['Type'] == 'Helm' else 'weapon.py'
            args = [source, original['Link'], conversion, '--prefab', template['PrefabName'], '--name', name,
                    '--validate-samples', ','.join(map(str, samples))]
            if original['Type'] != 'Helm':
                args += ['--expanded', '--faithful-effects', '--max-period', '10000', '--max-layers', '128']
            if cc: args += ['--rgb-cc']
            if interactive: args += ['--interactive-morph']
            n.run(converter, folder, *args)
            check = json.loads((conversion / 'validation.json').read_text())
            assert check['worst_mean'] <= 25 and check['max_clipped_alpha'] <= .5, check
            filename = Path(template['Bundle'].get('Filename') or template['Bundle'].get('FileName')).name
            n.run('build_armor.py', folder, conversion, '-', '--bundle', filename, '--project', n.PROJECT,
                  '--output-dir', output, '--validate', ','.join(map(str, samples)), '--prepare-only')
            entries.append(dict(source_id=original['id'], native_bundle_id=bid, key=name,
                kind='helm' if original['Type'] == 'Helm' else 'weapon', name=original['Name'], prefab=template['PrefabName'],
                original=original, conversion=str(conversion), bundle=str(output / filename), bundle_name=filename,
                frames=samples, sources=[dict(row['source'],relative=original['File'],path=str(source))],
                unity_input=str(n.PROJECT / 'Assets/Armors' / name / 'unity_input.json')))
            n.write(batch / 'prepared_plan.json', entries)
        except Exception as error:
            held.append(dict(source_id=original['id'],bundle_id=bid,name=original['Name'],type=original['Type'],reason=str(error)))
            n.write(batch / 'held_candidates.json', held)
            print('SPECIALTY_NEW_CASE', bid, original['Name'], str(error)[:220], flush=True)
        n.write(batch / 'feature_discoveries.json', discoveries)
    assert entries, 'No compatible specialty entries'
    build_armor.sync_project(str(n.PROJECT))
    job = batch / 'unity_batch.json';n.write(job,dict(inputs=[e['unity_input'] for e in entries]))
    startup = subprocess.STARTUPINFO(); startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW;startup.wShowWindow = subprocess.SW_HIDE
    print('SPECIALTY_UNITY_START', len(entries),flush=True)
    result = subprocess.run([build_armor.UNITY,'-batchmode','-quit','-projectPath',str(n.PROJECT),'-executeMethod',
        'ArmorBuilder.BuildMany','-armorBatchInput',str(job),'-logFile',str(batch/'unity_batch.log')],
        startupinfo=startup,creationflags=subprocess.CREATE_NO_WINDOW,timeout=7200,env=build_armor.unity_environment())
    print('SPECIALTY_UNITY_EXIT',result.returncode,flush=True)
    n.finalize(batch,entries,held,size)


def validate_selectors(batch, size):
    """Recheck activated states even when resuming after a completed Unity build."""
    from validate_morph import compare_gpu
    held = json.loads((batch/'held_candidates.json').read_text())
    prepared = json.loads((batch/'prepared_plan.json').read_text())
    accepted = []
    proofs = []
    for entry in json.loads((batch/'build_plan.json').read_text()):
        try:
            model = json.loads((Path(entry['conversion'])/'armor.json').read_text())
            if any(r.get('morph') for gd in model['genders'].values() for r in gd['renderers'].values()):
                compare_gpu(Path(entry['conversion']),Path(entry['bundle']).parent/'unity_validation',frames=entry['frames'])
                proofs.append(dict(bundle_id=entry['native_bundle_id'],status='passed',
                    sha256=n.sha(entry['bundle']),validation=str(Path(entry['bundle']).parent/'morph_gpu_validation.json')))
            accepted.append(entry)
        except Exception as error:
            held.append(dict(bundle_id=entry['native_bundle_id'],source_id=entry['source_id'],name=entry['name'],reason=str(error)))
    assert accepted, 'No builds pass activated-selector GPU gates'
    accepted_ids = {e['native_bundle_id'] for e in accepted}
    held = [e for e in held if e['bundle_id'] not in accepted_ids]
    n.write(batch/'build_plan.json',accepted)
    n.write(batch/'held_candidates.json',held)
    n.write(batch/'build_summary.json',dict(requested=size,prepared=len(prepared),passed=len(accepted),held=len(held)))
    n.write(batch/'specialty_validation.json',dict(status='passed',activated_selectors=proofs,
        bundles=[dict(bundle_id=e['native_bundle_id'],sha256=n.sha(e['bundle'])) for e in accepted]))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('discover', 'run'))
    parser.add_argument('--batch', default='native13')
    parser.add_argument('--size', type=int, default=18)
    parser.add_argument('--full', action='store_true', help='Inventory all original source-backed records in the supported categories')
    args = parser.parse_args()
    if args.mode == 'discover':
        discover(0 if args.full else 360); return
    assert not args.full, '--full applies to discovery only'
    assert n.re.fullmatch(r'native\d{2}', args.batch) and 1 <= args.size <= 64
    batch = n.BASE / args.batch; batch.mkdir(exist_ok=True)
    n.write(batch / 'pipeline_status.json',dict(status='running',phase='specialty_build',requested=args.size))
    try:
        if not (batch/'build_summary.json').exists():build(batch,args.size)
        if not (batch/'deployment_result.json').exists():validate_selectors(batch,args.size)
        n.pipeline(batch,args.size,universal=True)
    except Exception as error:
        n.write(batch / 'pipeline_status.json',dict(status='failed',phase='specialty',error=str(error)))
        raise


if __name__ == '__main__':
    main()
