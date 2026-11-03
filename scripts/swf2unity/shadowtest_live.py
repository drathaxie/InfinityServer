"""Bounded live registration of the user-requested shadowtest map."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
from datetime import datetime, timezone
import urllib.request

import db

ROOT = Path('/opt/infinity')
REGISTRY = ROOT / 'data/asset_bundles.json'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def services():
    return {name: dict(active=subprocess.check_output(['systemctl', 'is-active', name], text=True).strip(),
                       pid=subprocess.check_output(['systemctl', 'show', name, '-p', 'MainPID', '--value'], text=True).strip())
            for name in ('infinity-api', 'infinity-game')}


def preflight():
    assert db.BACKEND == 'mysql', 'Unexpected production backend'
    c = db.connect()
    try:
        registry = json.loads(REGISTRY.read_text())
        bid = 2100000
        while str(bid) in registry or c.execute('SELECT bundle_id FROM inf_asset_bundles WHERE bundle_id=?', (bid,)).fetchone():
            bid += 1
        mid = 2100000
        while c.execute('SELECT map_id FROM maps WHERE map_id=?', (mid,)).fetchone():
            mid += 1
        rows = [dict(r) for r in c.execute("SELECT * FROM maps WHERE LOWER(str_map_name) IN ('shadowtest','battleon')").fetchall()]
        engines = [dict(r) for r in c.execute("SELECT table_name,engine FROM information_schema.tables WHERE table_schema=DATABASE() AND table_name IN ('inf_maps','maps','inf_asset_bundles')").fetchall()]
        return dict(backend=db.BACKEND, registry_sha256=sha(REGISTRY), suggested_bundle_id=bid, suggested_map_id=mid,
                    relevant_maps=rows, services=services(), engines=engines,
                    existing_trial_bundles={k:v for k,v in registry.items() if 'shadowtest' in str(v).lower()},
                    server_hashes={name:sha(ROOT/'server'/name) for name in ('maps.py','db.py','webapi.py')})
    finally:
        c.close()


def publish(path, data, previous=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.shadowtest-new')
    with temporary.open('xb') as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    os.chmod(temporary, previous.st_mode & 0o777 if previous else 0o644)
    if previous:
        os.chown(temporary, previous.st_uid, previous.st_gid)
    os.replace(temporary, path)


def plan(payload_path):
    p = json.loads(payload_path.read_text())
    assert p['map_name'] == 'shadowtest'
    assert p['map_id'] == p['bundle_id'] == 2100000
    assert p['doc']['area']['strMapName'] == 'shadowtest'
    assert p['doc']['area']['PrefabName'] == 'map-shadowfall'
    assert set(p['doc']['cells']) == {'Enter', 'Garden1'}
    assert p['bundle']['Type'] == 'MAP' and p['bundle']['ID'] == p['bundle_id']
    assert p['bundle']['FileName'] == 'maps/custom/2100000_map-shadowtest.unity3d'
    assert all(p['bundle'][key] == 1 for key in ('VersionContent', 'VersionLive', 'VersionStage'))
    assert sha(REGISTRY) == p['preflight']['registry_sha256'], 'Registry drift: refresh preflight'
    assert services()['infinity-game'] == p['preflight']['services']['infinity-game'], 'Game service changed'
    assert all(sha(ROOT/'server'/name) == digest for name,digest in p['preflight']['server_hashes'].items())
    targets = {}
    for platform in ('windows', 'android'):
        binary = payload_path.parent / (platform + '.unity3d')
        assert binary.read_bytes().startswith(b'UnityFS') and sha(binary) == p['files'][platform]['sha256']
        target = ROOT / 'content/game/assetbundles' / platform / 'maps/custom/2100000_map-shadowtest/1/2100000_map-shadowtest.unity3d'
        assert not target.exists(), 'Content target is occupied'
        targets[platform] = target
    map_file = ROOT / 'data/maps/shadowtest.json'
    assert not map_file.exists(), 'Existing map file must not be replaced'
    registry = json.loads(REGISTRY.read_text())
    assert str(p['bundle_id']) not in registry
    c = db.connect()
    try:
        assert db.BACKEND == 'mysql'
        assert c.execute('SELECT map_id FROM maps WHERE map_id=? OR LOWER(str_map_name)=?', (p['map_id'], 'shadowtest')).fetchone() is None
        assert c.execute('SELECT bundle_id FROM inf_asset_bundles WHERE bundle_id=?', (p['bundle_id'],)).fetchone() is None
        assert set(r['engine'] for r in p['preflight']['engines']) == {'InnoDB'}
    finally:
        c.close()
    return p, registry, targets, map_file


def apply(payload_path, dry_run=False):
    p, registry, targets, map_file = plan(payload_path)
    if dry_run:
        return dict(status='preflight_passed', map='shadowtest', map_id=p['map_id'], bundle_id=p['bundle_id'],
                    files={k:str(v) for k,v in targets.items()}, game_restart_required=False, api_restart_required=True)
    backup = ROOT / 'backups' / datetime.now(timezone.utc).strftime('shadowtest-%Y%m%dT%H%M%SZ')
    backup.mkdir(mode=0o700)
    stat = REGISTRY.stat()
    shutil.copy2(REGISTRY, backup/'asset_bundles.json')
    shutil.copy2(payload_path, backup/'deployment_payload.json')
    shutil.copy2(Path(__file__), backup/'shadowtest_live.py')
    (backup/'before.json').write_text(json.dumps(dict(map_before=None,bundle_before=None,map_file_before=None,
                                                    services=services()), indent=2))
    doc = p['doc']
    area = doc['area']
    meta = {k:v for k,v in area.items() if k not in ('monBranch','uoBranch')}
    registry[str(p['bundle_id'])] = p['bundle']
    changed = []
    committed = False
    api_stopped = False
    c = db.connect()
    try:
        c.execute('SELECT map_id FROM maps WHERE map_id=? OR LOWER(str_map_name)=? FOR UPDATE', (p['map_id'],'shadowtest')).fetchall()
        assert c.execute('SELECT map_id FROM maps WHERE map_id=? OR LOWER(str_map_name)=?', (p['map_id'],'shadowtest')).fetchone() is None
        assert c.execute('SELECT bundle_id FROM inf_asset_bundles WHERE bundle_id=? FOR UPDATE', (p['bundle_id'],)).fetchone() is None
        assert sha(REGISTRY) == p['preflight']['registry_sha256']
        api_stopped = True
        subprocess.run(['systemctl','stop','infinity-api'], check=True, timeout=30)
        assert sha(REGISTRY) == p['preflight']['registry_sha256']
        c.execute('INSERT INTO inf_asset_bundles(bundle_id,name,type,filename,version_content,version_stage,version_live,dependency_id) VALUES(?,?,?,?,?,?,?,?)',
                  (p['bundle_id'],'shadowtest','MAP',p['bundle']['FileName'],1,1,1,0))
        c.execute('INSERT INTO maps(map_id,area_name,str_map_name,display_name,prefab_name,soundtrack_id,int_type,bundle,quest_ids,raw,doc) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                  (p['map_id'],area['areaName'],'shadowtest',area['DisplayName'],area['PrefabName'],None,area['intType'],
                   json.dumps(area['Bundle']), '[]',json.dumps(meta),json.dumps(doc)))
        for platform,target in targets.items():
            publish(target,(payload_path.parent/(platform+'.unity3d')).read_bytes()); changed.append(target)
            assert sha(target) == p['files'][platform]['sha256']
        publish(map_file,json.dumps(doc,indent=2).encode()); changed.append(map_file)
        publish(REGISTRY,json.dumps(registry,separators=(',',':')).encode(),stat); changed.append(REGISTRY)
        check = json.loads(REGISTRY.read_text())
        assert check.pop(str(p['bundle_id'])) == p['bundle']
        assert check == json.loads((backup/'asset_bundles.json').read_text()), 'Unrelated registry records changed'
        c.commit(); committed=True
    except Exception:
        c.rollback()
        if not committed:
            for path in reversed(changed):
                if path == REGISTRY:
                    publish(REGISTRY,(backup/'asset_bundles.json').read_bytes(),stat)
                else:
                    assert path == map_file or path in targets.values()
                    path.unlink()
        raise
    finally:
        c.close()
        if api_stopped:
            subprocess.run(['systemctl','start','infinity-api'],check=True,timeout=40)
    result = dict(status='applied',map='shadowtest',map_id=p['map_id'],bundle_id=p['bundle_id'],backup=str(backup),
                  public_content={platform:'https://content.130-162-189-229.sslip.io/'+target.relative_to(ROOT/'content').as_posix() for platform,target in targets.items()},
                  services=services(),game_pid_preserved=services()['infinity-game']['pid']==p['preflight']['services']['infinity-game']['pid'])
    (backup/'result.json').write_text(json.dumps(result,indent=2))
    return result


def verify(payload_path):
    p = json.loads(payload_path.read_text())
    c = db.connect()
    try:
        import maps
        area = maps.area_payload('shadowtest',c)
        assert area['Bundle'] == p['doc']['area']['Bundle'] and area['PrefabName'] == 'map-shadowfall'
        for frame,pad in (('Enter','Spawn'),('Garden1','Bottom'),('Enter','Right')):
            cell = maps.cell_payload('shadowtest',frame,pad,c)
            assert cell['Frame']==frame and cell['Pad']==pad
        row = dict(c.execute('SELECT * FROM inf_asset_bundles WHERE bundle_id=?',(p['bundle_id'],)).fetchone())
        assert row['filename'] == p['bundle']['FileName'] and row['version_live']==1
    finally:
        c.close()
    downloads = {}
    for platform in ('windows','android'):
        url = 'https://content.130-162-189-229.sslip.io/game/assetbundles/'+platform+'/maps/custom/2100000_map-shadowtest/1/2100000_map-shadowtest.unity3d'
        with urllib.request.urlopen(url,timeout=30) as response:
            binary = response.read()
            assert response.status==200 and hashlib.sha256(binary).hexdigest()==p['files'][platform]['sha256']
            downloads[platform] = dict(url=url,bytes=len(binary),sha256=hashlib.sha256(binary).hexdigest())
    with urllib.request.urlopen('https://divinityarts.mooo.com/data/GetAssetBundlesByIDs?ids='+str(p['bundle_id']),timeout=30) as response:
        bundle = next(b for b in json.loads(response.read()) if b['ID']==p['bundle_id'])
        assert (bundle.get('FileName') or bundle.get('Filename')) == p['bundle']['FileName']
        assert bundle['CRCPC']==p['bundle']['CRCPC'] and bundle['CRCAndroid']==p['bundle']['CRCAndroid']
        assert bundle['VersionLive']==bundle['VersionContent']==bundle['VersionStage']==1
    state = services()
    assert all(v['active']=='active' for v in state.values())
    assert state['infinity-game']['pid']==p['preflight']['services']['infinity-game']['pid']
    return dict(status='passed',map='shadowtest',area_payload=area,public_bundle=bundle,downloads=downloads,services=state,
                game_pid_preserved=True,proof_boundary='Public platform files, bundle API and server map payloads verified. Installed-client loading, walking and rendering remain separate checks.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('preflight','dry-run','apply','verify'), default='preflight', nargs='?')
    parser.add_argument('--payload',type=Path)
    args = parser.parse_args()
    if args.mode=='preflight':
        result=preflight()
    elif args.mode=='verify':
        result=verify(args.payload)
    else:
        result=apply(args.payload,dry_run=args.mode=='dry-run')
    print(json.dumps(result, indent=2, default=str))
