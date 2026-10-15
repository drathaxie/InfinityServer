"""Small conversion-test batches: discover, capture, build, and guarded publication."""
import argparse
import hashlib
import io
import json
from pathlib import Path
import shlex
import subprocess
import sys
import tarfile
from copy import deepcopy
from collections import Counter

from trial_host import remote, server_script, ROOT

BATCH = ROOT / 'outputs/flash_converter_trial/batch02'
REMOTE_BATCH = '/tmp/infinity-conversion-batch02-20261012'
RESUME = False
# Same limits as ArmorBuilder.CheckTextureBudget, checked here on the serialized bundle.
MAX_TEXTURE_DIMENSION = 4096
MAX_BUNDLE_TEXTURE_BYTES = 24 * 1024 * 1024
# Published before the budget existed and too large to meet it without a new conversion; each may not grow.
# 5091 Elemental Dracomancer: two genders of player-color maps (needs a half-resolution conversion).
TEXTURE_BUDGET_WAIVERS = {5091: 65_765_264}


def discover():
    BATCH.mkdir(parents=True, exist_ok=True)
    script = r'''
import json,db
c=db.connect()
try:
 q="""SELECT id,Name,File,Link,Type FROM items
 WHERE bundle IS NULL AND File<>'' AND (
 (Type IN ('Armor','Class') AND (Name LIKE '%Chrono%' OR Name LIKE '%Revenant%' OR Name LIKE '%Highlord%' OR Name LIKE '%Dragonlord%' OR Name LIKE '%ArchPaladin%' OR Name='DoomKnight'))
 OR (Type='Sword' AND (Name LIKE '%Burning Blade%' OR Name LIKE '%Necrotic Sword%' OR Name LIKE '%Blinding Light%' OR Name LIKE '%DragonBlade%' OR Name LIKE '%Phoenix Blade%')))
 ORDER BY id LIMIT 100"""
 print(json.dumps([dict(r) for r in c.execute(q).fetchall()],default=str))
finally:c.close()
'''
    rows = json.loads(server_script('-', data=script.encode()))
    (BATCH / 'candidates.json').write_text(json.dumps(rows, indent=2))
    for r in rows:
        print(r['id'], r['Type'], r['Name'], r['File'], r['Link'])


def capture():
    plan = json.loads((BATCH / 'plan.json').read_text())
    candidates = {r['id']: r for r in json.loads((BATCH / 'candidates.json').read_text())}
    for entry in plan:
        original = candidates[entry['source_id']]
        folder = BATCH / entry['key'] / 'source'
        folder.mkdir(parents=True, exist_ok=True)
        filename = original['File']
        if '..' in filename or '\\' in filename:
            raise ValueError(filename)
        paths = ['classes/' + g + '/' + Path(filename).name for g in ('M', 'F')] if entry['kind'] == 'armor' else [filename]
        sources = []
        for relative in paths:
            binary = remote('cat -- ' + shlex.quote('/var/www/swordshine/gamefiles/' + relative))
            assert binary[:3] in (b'FWS', b'CWS', b'ZWS')
            target = folder / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(binary)
            sources.append(dict(relative=relative, path=str(target), bytes=len(binary), sha256=hashlib.sha256(binary).hexdigest()))
        entry['original'] = original
        entry['sources'] = sources
        print(entry['key'], 'captured', [(s['relative'], s['bytes']) for s in sources], flush=True)
    (BATCH / 'captured_plan.json').write_text(json.dumps(plan, indent=2))


def build():
    assert not (BATCH / 'deployment_result.json').exists(), 'Published batch is immutable; create a new batch'
    captured = {p['key']: p for p in json.loads((BATCH / 'captured_plan.json').read_text())}
    plan = ([captured[k] for k in ('ChronoStriker', 'DarkMatterDragonBlade')] if BATCH.name == 'batch02'
            else list(captured.values()))
    template = ROOT / 'outputs/flash_converter_trial/skin_fix/template'
    project = ROOT / 'outputs/flash_converter_trial/FlyingMoglin/UnityProject'
    previous = {e['key']: e for e in json.loads((BATCH / 'build_plan.json').read_text())} if RESUME else {}
    built = []
    for entry in plan:
        folder = BATCH / entry['key']
        conversion = folder / 'conversion'
        for source in entry['sources']:
            assert hashlib.sha256(Path(source['path']).read_bytes()).hexdigest() == source['sha256']
        if entry['key'] in previous:
            cached = previous[entry['key']]
            assert cached['sources'] == entry['sources'] and cached['source_id'] == entry['source_id']
            audit(cached)
            built.append(cached)
            print('REUSED validated build',entry['key'],flush=True)
            continue
        if entry['kind'] == 'weapon':
            import rig, swfparse as sp
            movie = sp.load(entry['sources'][0]['path'])
            source_rig = rig.Rig(movie)
            cid = next(c for c,n in movie.symbols.items() if n == entry['original']['Link'])
            layers = source_rig.decompose(cid)
            screen = dict(layers=[dict(kind=l.kind,period=l.period) for l in layers],holds=source_rig.notes)
            (folder/'source_screen.json').write_text(json.dumps(screen,indent=2))
            assert not source_rig.notes and len(layers) <= 32, screen
        def run(script, *args):
            cmd = [sys.executable, str(ROOT / 'scripts/swf2unity' / script)] + list(map(str, args))
            print('running', entry['key'], script, flush=True)
            result = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
            (folder / (script.removesuffix('.py') + '.log')).write_text(result.stdout + result.stderr)
            if result.returncode:
                raise RuntimeError(result.stdout[-2500:] + result.stderr[-2500:])
            print(result.stdout[-1500:], flush=True)
        if entry['kind'] == 'armor':
            sources = {Path(s['path']).parent.name: s['path'] for s in entry['sources']}
            run('armor.py', sources['M'], sources['F'], template, conversion,
                '--name', entry['key'], '--max-period', 1200)
            run('validate_armor.py', conversion, sources['M'], sources['F'], '--frames', 12, '--sheet')
            frames = '0,7,19,59,99,199,299,1099,1100,2199,2200'
        else:
            run('weapon.py', entry['sources'][0]['path'], entry['original']['Link'], conversion,
                '--prefab', entry['prefab'], '--name', entry['key'], '--validate-frames', 36)
            frames = '0,13,29,59,99,199,213,214,427,428'
        converted = json.loads((conversion / 'armor.json').read_text())
        holds = [n for n in converted.get('notes', []) if any(w in n.lower() for w in ('capped', 'ignored', 'without', 'flattening', 'drawn normally'))]
        assert not holds, holds
        entry['bundle_name'] = entry['key'].lower() + '_' + BATCH.name + '_flash_trial.unity3d'
        run('build_armor.py', conversion, template if entry['kind'] == 'armor' else '-',
            '--bundle', entry['bundle_name'], '--project', project, '--output-dir', folder / 'bundle',
            '--validate', frames)
        entry['conversion'] = str(conversion)
        entry['bundle'] = str(folder / 'bundle' / entry['bundle_name'])
        entry['bundle_sha256'] = hashlib.sha256(Path(entry['bundle']).read_bytes()).hexdigest()
        built.append(entry)
        (BATCH / 'build_plan.json').write_text(json.dumps(built, indent=2))
        print('BUILT', entry['name'], Path(entry['bundle']).stat().st_size, flush=True)


def audit(entry):
    import re
    import UnityPy
    binary = Path(entry['bundle'])
    assert hashlib.sha256(binary.read_bytes()).hexdigest() == entry['bundle_sha256']
    folder = binary.parent
    gpu = json.loads((folder / 'unity_validation_summary.json').read_text())
    contract = json.loads((folder / 'bundle_contract.json').read_text())
    assert gpu['status'] == contract['status'] == 'passed' and not gpu['failures']
    assert gpu['expected_captures'] == gpu['compared_captures'] > 0 and not contract['dependencies']
    env = UnityPy.load(str(binary))
    data = json.loads((Path(entry['conversion']) / 'armor.json').read_text())
    source = json.loads((Path(entry['conversion']) / 'validation.json').read_text())
    if entry['kind'] == 'armor':
        assert all(not values for values in data.get('rig_notes',{}).values()),'Unresolved armor source timeline notes'
        assert source and all(v['frames'] > 0 and v['worst_frame_mean'] <= 25 and v['clipped'] <= 0.5
                              for v in source.values()), 'source fidelity or clipping gate failed'
        source_error = max(v['worst_frame_mean'] for v in source.values())
    else:
        assert source['frames'] > 0 and source['worst_mean'] <= 25 and source['max_clipped_alpha'] <= 0.5
        source_error = source['worst_mean']
    allowed = {'ItemSlots', 'CustomizableSlot', 'NoFlashWhenHit'}
    components, textures, scripts = Counter(), [], []
    for obj in env.objects:
        if obj.type.name == 'MonoScript':
            script = obj.read()
            assert script.m_ClassName in allowed and script.m_Namespace == ''
            assert script.m_AssemblyName.removesuffix('.dll') == 'Assembly-CSharp'
            scripts.append(script.m_ClassName)
        elif obj.type.name == 'MonoBehaviour':
            components[obj.read().m_Script.read().m_ClassName] += 1
        elif obj.type.name == 'Texture2D':
            texture = obj.read()
            textures.append(dict(name=texture.m_Name, width=texture.m_Width, height=texture.m_Height,
                bytes=texture.m_CompleteImageSize, format=texture.m_TextureFormat, readable=bool(texture.m_IsReadable)))
    containers = list(env.container)
    assert len(containers) == (2 if entry['kind'] == 'armor' else (1 if entry['kind']!='helm' or entry['prefab']=='HelmGO' else 2))
    if entry['kind'] == 'armor':
        assert all(any(n.endswith('armorbones_' + g + '.prefab') for n in containers) for g in ('m', 'f'))
        assert components['ItemSlots'] == 2
    else:
        assert any(c.endswith(entry['prefab'].lower() + '.prefab') for c in containers)
        roots={env.container[c].path_id for c in containers}
        assert components['ItemSlots'] == (len(roots) if entry['kind'] == 'helm' else 0)
        if entry['kind'] == 'helm':
            assert any(c.endswith('/helmgo.prefab') for c in containers), 'Native character loader hard-codes HelmGO'
            assert contract.get('helmAttachmentContract')==2, 'Loaded native helm attachment proof missing'
            # setupHelm reparents these direct children before disabling the
            # data root. Animation therefore has to live on the helm child.
            root = env.container[next(c for c in containers if c.endswith('/helmgo.prefab'))].read()
            root_components = [c.component for c in root.m_Component]
            assert not any(c.type.name in ('SpriteRenderer', 'Animator') for c in root_components)
            transform = next(c.read() for c in root_components if c.type.name == 'Transform')
            children = {t.read().m_GameObject.read().m_Name: t.read() for t in transform.m_Children}
            assert set(children) == {'helm', 'fronthair', 'backhair'}
            for name, slot in (('helm', 37), ('fronthair', 27), ('backhair', 1)):
                child = children[name].m_GameObject.read()
                child_components = [c.component for c in child.m_Component]
                sprites = [c.read_typetree() for c in child_components if c.type.name == 'SpriteRenderer']
                assert len(sprites) == 1
                slots = [c.read_typetree()['slot'] for c in child_components
                         if c.type.name == 'MonoBehaviour' and c.read().m_Script.read().m_ClassName == 'CustomizableSlot']
                assert slots == [slot]
                if name == 'helm':
                    assert sprites[0]['m_Sprite']['m_PathID'] and sprites[0]['m_Materials'][0]['m_PathID']
                    renderer = data['genders']['w']['renderers']['helm']
                    animated = any(l['period'] > 1 for l in (renderer.get('layers') or [])) or renderer.get('morph')
                    assert not animated or any(c.type.name == 'Animator' for c in child_components)
                else:
                    assert not sprites[0]['m_Sprite']['m_PathID'], 'unexpected extra hair artwork in full morph'
    layered = sum(bool(r.get('layers')) for gd in data['genders'].values() for r in gd['renderers'].values())
    assert components['NoFlashWhenHit'] == layered * (len(roots) if entry['kind']=='helm' else 1)
    crc = re.search(r'^CRC: (\d+)$', binary.with_suffix('.unity3d.manifest').read_text(), re.MULTILINE)
    assert crc and int(crc[1]) == contract['bundleCRC']
    texture_bytes = sum(t['bytes'] for t in textures)
    assert texture_bytes == contract['textureBytes']
    # Client memory budget: a 20-player room holds every equipped bundle at once.
    assert not any(t['readable'] for t in textures), 'CPU-readable texture copies in bundle'
    assert max(max(t['width'], t['height']) for t in textures) <= MAX_TEXTURE_DIMENSION, 'Texture exceeds 4096 px'
    budget = TEXTURE_BUDGET_WAIVERS.get(entry.get('native_bundle_id'), MAX_BUNDLE_TEXTURE_BYTES)
    assert texture_bytes <= budget, f'Bundle texture memory {texture_bytes} exceeds budget {budget}'
    periods = sorted({l['period'] for gd in data['genders'].values() for r in gd['renderers'].values() for l in r.get('layers') or []})
    if entry['key'] == 'ChronoStriker': assert 1100 in periods
    if entry['kind'] == 'armor':
        for obj in env.objects:
            if obj.type.name == 'MonoBehaviour' and obj.read().m_Script.read().m_ClassName == 'ItemSlots':
                head = [s for s in obj.read_typetree()['slots'] if s['slot'] == 23]
                if data.get('morph'):
                    root_name = obj.read().m_GameObject.read().m_Name
                    gender = root_name.rsplit('_', 1)[-1]
                    assert gender in data['genders']
                    expected_head_entries = sum(s['slot'] == 23 for s in data['genders'][gender]['itemslots'])
                    assert len(head) == expected_head_entries > 0, 'source head atlas entries differ from converted input'
                else:
                    assert len(head) == 1 and head[0]['CCMask']['m_PathID'], 'head skin mask missing in bundle'
    result = dict(status='passed', bytes=binary.stat().st_size, texture_bytes=texture_bytes,
        sha256=entry['bundle_sha256'], crc=contract['bundleCRC'], components=dict(components),
        native_scripts=scripts, containers=containers, periods=periods,
        gpu_worst_mean_error=gpu['worst_mean_error'], source_worst_mean_error=source_error)
    (folder / 'independent_audit.json').write_text(json.dumps(result, indent=2))
    return result


def preflight():
    assert not (BATCH / 'deployment_result.json').exists(), 'This batch already has an application result'
    plan = json.loads((BATCH / 'build_plan.json').read_text())
    assert 1 <= len(plan) <= 12
    audits = [audit(e) for e in plan]
    source_plan = [dict(id=e['source_id'], relatives=[s['relative'] for s in e['sources']],
                        name=e['name'], prefab=e['prefab'], native_bundle_id=e.get('native_bundle_id')) for e in plan]
    script = ('SOURCES = ' + repr(source_plan) + '\n').encode() + (ROOT / 'scripts/swf2unity/batch_preflight.py').read_bytes()
    pf = json.loads(server_script('-', data=script))
    (BATCH / 'live_preflight.json').write_text(json.dumps(pf, indent=2))
    assert pf['services'] == ['active', 'active']
    entries = []
    for index, (entry, evidence) in enumerate(zip(plan, audits)):
        original = pf['originals'][str(entry['source_id'])]
        assert original['File'] == entry['original']['File'] and original['Link'] == entry['original']['Link']
        for source in entry['sources']: assert pf['source_hashes'][source['relative']] == source['sha256']
        iid, bid = pf['item_ids'][index], pf['bundle_ids'][index]
        weapon_type = {'Sword': (6, 'items/swords/'), 'Polearm': (20, 'items/polearms/'), 'Helm': (12, 'items/helms/')}
        item_type, category = (22, 'armors/') if entry['kind'] == 'armor' else weapon_type[original['Type']]
        filename = category + entry['bundle_name']
        bundle = dict(ID=bid, Name=entry['name'], Type='ARMOR' if entry['kind'] == 'armor' else 'ITEM',
            FileName=filename, Version=1, VersionContent=1, VersionStage=1, VersionLive=1,
            DependencyID=0, CRCPC=str(evidence['crc']), _cdnvL=1)
        inline = {k:v for k,v in bundle.items() if k not in ('FileName','_cdnvL')}
        inline['Filename'] = filename
        item = deepcopy(pf['templates'][str(entry['source_id'])])
        item.update(ID=iid, Name=entry['name'], Description=BATCH.name + ' Flash-to-Unity conversion trial.',
            ItemType=item_type, EquipSpot=7 if entry['kind']=='armor' else (3 if entry['kind']=='helm' else 2),
            Linkage=original['Link'], Filename=original['File'], PrefabName=entry['prefab'],
            MobileCompatibility=1, Level=1, Cost=0, Coins=False, UpgradeOnly=False,
            isClass=False, House=False, HouseInventory=False, ReqQuests=[], strReqQuests='',
            requiredClass=0, requiredCP=0, RequiredRep=0, Bundle=inline)
        relative = Path(filename)
        entries.append(dict(item=item, bundle=bundle, source_id=entry['source_id'], original=original,
            sources=[{k:s[k] for k in ('relative','sha256','bytes')} for s in entry['sources']],
            bundle_sha256=evidence['sha256'],
            bundle_target=(Path('/opt/infinity/content/game/assetbundles/windows')/relative.parent/relative.stem/'1'/relative.name).as_posix()))
    payload = dict(batch=BATCH.name, character=pf['character'], entries=entries,
        catalog_sha256=pf['catalog_sha256'], seed_sha256=pf['seed_sha256'],
        reason='User requested these Flash conversion trials; granted unequipped to redux while preserving source records.')
    (BATCH / 'deployment_payload.json').write_text(json.dumps(payload, indent=2))
    print(json.dumps(dict(status='prepared', items=[(e['item']['ID'],e['item']['Name']) for e in entries],
        build_evidence=audits), indent=2))


def stage():
    assert not (BATCH / 'deployment_result.json').exists()
    plan = json.loads((BATCH / 'build_plan.json').read_text())
    files = {Path(e['bundle']).name:Path(e['bundle']) for e in plan}
    files.update({'payload.json': BATCH/'deployment_payload.json'})
    for name in ('deploy_batch.py','deploy_pet_trial.py','verify_batch.py'):
        files[name] = ROOT/'scripts/swf2unity'/name
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode='w') as archive:
        for name, path in files.items(): archive.add(path,arcname=name)
    remote('mkdir -p '+REMOTE_BATCH+' && tar -xf - -C '+REMOTE_BATCH,stream.getvalue())
    result = json.loads(server_script(REMOTE_BATCH+'/deploy_batch.py', '--payload '+REMOTE_BATCH+'/payload.json'))
    assert result['status']=='preflight_passed'
    (BATCH/'deployment_dry_run.json').write_text(json.dumps(result,indent=2))
    print(json.dumps(result))


def apply():
    assert not (BATCH/'deployment_result.json').exists()
    assert json.loads((BATCH/'deployment_dry_run.json').read_text())['status']=='preflight_passed'
    result = json.loads(server_script(REMOTE_BATCH+'/deploy_batch.py', '--payload '+REMOTE_BATCH+'/payload.json --apply'))
    (BATCH/'deployment_result.json').write_text(json.dumps(result,indent=2))
    print(json.dumps(result))


def verify():
    result = json.loads(server_script(REMOTE_BATCH+'/verify_batch.py', '--payload '+REMOTE_BATCH+'/payload.json'))
    (BATCH/'live_verification.json').write_text(json.dumps(result,indent=2))
    print(json.dumps(result))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('discover', 'capture', 'build', 'preflight', 'stage', 'apply', 'verify'))
    parser.add_argument('--batch', default='batch02')
    parser.add_argument('--resume', action='store_true', help='reuse independently audited completed builds')
    args = parser.parse_args()
    import re
    assert re.fullmatch(r'batch\d{2}', args.batch)
    BATCH = ROOT / 'outputs/flash_converter_trial' / args.batch
    RESUME = args.resume
    REMOTE_BATCH = '/tmp/infinity-conversion-' + args.batch + '-20261012'
    globals()[args.mode]()
