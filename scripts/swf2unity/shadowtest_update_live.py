"""Guarded versioned update for the existing shadowtest map only."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
from datetime import datetime, timezone
import urllib.request

sys.path.insert(0,'/tmp/infinity-shadowtest-20261102')
from shadowtest_live import ROOT, REGISTRY, sha, publish, services
import db


def plain(value):return json.loads(json.dumps(value,default=str))


def preflight():
    assert db.BACKEND=='mysql'
    c=db.connect()
    try:
        row=plain(dict(c.execute('SELECT * FROM maps WHERE map_id=?',(2100000,)).fetchone()))
        shared=plain(dict(c.execute('SELECT * FROM inf_asset_bundles WHERE bundle_id=?',(2100000,)).fetchone()))
        bundle=json.loads(REGISTRY.read_text())['2100000']
        assert row['str_map_name']=='shadowtest' and bundle['Name']=='shadowtest'
        assert bundle['VersionLive']==shared['version_live'] and bundle['VersionLive'] in (1,2,3,4,5,6,7,8,9,10)
        assert json.loads(row['doc'])==json.loads((ROOT/'data/maps/shadowtest.json').read_text())
        return dict(map=row,shared_bundle=shared,bundle=bundle,registry_sha256=sha(REGISTRY),
                    map_file_sha256=sha(ROOT/'data/maps/shadowtest.json'),services=services(),
                    assetwire_sha256=sha(ROOT/'server/assetwire.py'))
    finally:c.close()


def plan(payload_path):
    p=json.loads(payload_path.read_text());assert p['map_id']==p['bundle_id']==2100000 and p['version'] in (2,3,4,5,6,7,8,9,10,11)
    version=p['version']
    assert p['bundle']['FileName']=='maps/custom/2100000_map-shadowtest.unity3d'
    assert p['bundle']['VersionLive']==p['bundle']['VersionStage']==p['bundle']['VersionContent']==version
    before=preflight();assert before==p['before'],'Live shadowtest/catalog drift: refresh preflight'
    assert before['bundle']['VersionLive']==version-1
    targets={}
    for platform in ('windows','android'):
        binary=payload_path.parent/(platform+'.unity3d')
        assert sha(binary)==p['files'][platform]['sha256'] and binary.read_bytes().startswith(b'UnityFS')
        target=ROOT/'content/game/assetbundles'/platform/('maps/custom/2100000_map-shadowtest/'+str(version)+'/2100000_map-shadowtest.unity3d')
        assert not target.exists(),'New version target already exists'
        targets[platform]=target
    return p,targets


def apply(payload_path,dry_run=False):
    p,targets=plan(payload_path)
    version=p['version']
    if dry_run:return dict(status='preflight_passed',map='shadowtest',version=version,files={k:str(v) for k,v in targets.items()})
    backup=ROOT/'backups'/datetime.now(timezone.utc).strftime('shadowtest-v'+str(version)+'-%Y%m%dT%H%M%SZ');backup.mkdir(mode=0o700)
    map_file=ROOT/'data/maps/shadowtest.json';registry_stat=REGISTRY.stat();map_stat=map_file.stat()
    for name,path in (('asset_bundles.json',REGISTRY),('shadowtest.json',map_file),('payload.json',payload_path)):
        shutil.copy2(path,backup/name)
    (backup/'before.json').write_text(json.dumps(p['before'],indent=2))
    shutil.copy2(Path(__file__),backup/'shadowtest_update_live.py')
    registry=json.loads(REGISTRY.read_text());registry['2100000']=p['bundle']
    doc=p['doc'];area=doc['area'];meta={k:v for k,v in area.items() if k not in ('monBranch','uoBranch')}
    c=db.connect();changed=[];committed=False;stopped=False
    try:
        row=plain(dict(c.execute('SELECT * FROM maps WHERE map_id=? FOR UPDATE',(2100000,)).fetchone()))
        shared=plain(dict(c.execute('SELECT * FROM inf_asset_bundles WHERE bundle_id=? FOR UPDATE',(2100000,)).fetchone()))
        assert row==p['before']['map'] and shared==p['before']['shared_bundle']
        assert sha(REGISTRY)==p['before']['registry_sha256'] and sha(map_file)==p['before']['map_file_sha256']
        stopped=True;subprocess.run(['systemctl','stop','infinity-api'],check=True,timeout=30)
        assert sha(REGISTRY)==p['before']['registry_sha256']
        c.execute('UPDATE inf_asset_bundles SET version_content=?,version_stage=?,version_live=? WHERE bundle_id=?',(version,version,version,2100000))
        c.execute('UPDATE maps SET bundle=?,raw=?,doc=? WHERE map_id=?',(json.dumps(area['Bundle']),json.dumps(meta),json.dumps(doc),2100000))
        for platform,target in targets.items():
            publish(target,(payload_path.parent/(platform+'.unity3d')).read_bytes());changed.append(target)
        publish(map_file,json.dumps(doc,indent=2).encode(),map_stat);changed.append(map_file)
        publish(REGISTRY,json.dumps(registry,separators=(',',':')).encode(),registry_stat);changed.append(REGISTRY)
        readback=json.loads(REGISTRY.read_text());readback.pop('2100000')
        previous=json.loads((backup/'asset_bundles.json').read_text());previous.pop('2100000')
        assert readback==previous,'Unrelated catalog records changed'
        after=plain(dict(c.execute('SELECT * FROM maps WHERE map_id=?',(2100000,)).fetchone()))
        old=dict(row)
        for key in ('bundle','raw','doc'):after.pop(key);old.pop(key)
        assert after==old,'Unrelated map fields changed'
        c.commit();committed=True
    except Exception:
        c.rollback()
        if not committed:
            for path in reversed(changed):
                if path==REGISTRY:
                    current=json.loads(REGISTRY.read_text())
                    assert current['2100000']==p['bundle'],'Owned registry entry changed during rollback'
                    current['2100000']=p['before']['bundle']
                    publish(path,json.dumps(current,separators=(',',':')).encode(),REGISTRY.stat())
                elif path==map_file:publish(path,(backup/'shadowtest.json').read_bytes(),map_stat)
                else:
                    assert path in targets.values();path.unlink()
        raise
    finally:
        c.close()
        if stopped:subprocess.run(['systemctl','start','infinity-api'],check=True,timeout=40)
    result=dict(status='applied',map='shadowtest',version=version,backup=str(backup),services=services(),
                game_pid_preserved=services()['infinity-game']['pid']==p['before']['services']['infinity-game']['pid'])
    (backup/'result.json').write_text(json.dumps(result,indent=2));return result


def verify(payload_path):
    p=json.loads(payload_path.read_text());downloads={};version=p['version']
    for platform in ('windows','android'):
        url='https://content.130-162-189-229.sslip.io/game/assetbundles/'+platform+'/maps/custom/2100000_map-shadowtest/'+str(version)+'/2100000_map-shadowtest.unity3d'
        with urllib.request.urlopen(url,timeout=30) as response:
            data=response.read();assert response.status==200 and hashlib.sha256(data).hexdigest()==p['files'][platform]['sha256']
            downloads[platform]=dict(url=url,sha256=hashlib.sha256(data).hexdigest(),bytes=len(data))
    with urllib.request.urlopen('https://divinityarts.mooo.com/data/GetAssetBundlesByIDs?ids=2100000',timeout=30) as response:
        bundle=next(b for b in json.loads(response.read()) if b['ID']==2100000)
        assert all(bundle[k]==version for k in ('VersionLive','VersionStage','VersionContent'))
        assert bundle['CRCPC']==p['bundle']['CRCPC'] and bundle['CRCAndroid']==p['bundle']['CRCAndroid']
    c=db.connect()
    try:
        import maps,assetwire
        assert maps.area_payload('shadowtest',c)['Bundle']==p['doc']['area']['Bundle']
        # The running game can retain the previous map doc. Its existing
        # version serializer refreshes version fields from the current catalog.
        cached=assetwire.normalize(json.loads(p['before']['map']['doc'])['area'])
        assert cached['Bundle']['VersionLive']==cached['Bundle']['VersionContent']==cached['Bundle']['VersionStage']==version
        assert sha(ROOT/'server/assetwire.py')==p['before']['assetwire_sha256']
    finally:c.close()
    state=services();assert all(v['active']=='active' for v in state.values())
    assert state['infinity-game']['pid']==p['before']['services']['infinity-game']['pid']
    return dict(status='passed',map='shadowtest',version=version,downloads=downloads,public_bundle=bundle,services=state,
                cached_map_version_normalization_passed=True,game_pid_preserved=True,
                proof_boundary='Public files/API, source-comparison and local physics verified. New live scale, motion and visual playback require installed-client observation.')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('mode',choices=('preflight','dry-run','apply','verify'),default='preflight',nargs='?');parser.add_argument('--payload',type=Path)
    args=parser.parse_args()
    result=preflight() if args.mode=='preflight' else verify(args.payload) if args.mode=='verify' else apply(args.payload,args.mode=='dry-run')
    print(json.dumps(result,indent=2,default=str))
