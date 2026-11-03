"""Prepare, stage and publish the explicitly requested shadowtest map only."""
import argparse
import hashlib
import io
import json
from pathlib import Path
import re
import tarfile

import trial_host as host

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT/'outputs/map_converter_trial/shadowfall'
OUT = SOURCE/'live_shadowtest'
REMOTE = '/tmp/infinity-shadowtest-20261102'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def prepare():
    preflight = json.loads((OUT/'preflight.json').read_text())
    assert not any(r['str_map_name'].lower()=='shadowtest' for r in preflight['relevant_maps'])
    assert not preflight['existing_trial_bundles']
    assert preflight['suggested_map_id']==preflight['suggested_bundle_id']==2100000
    proof = json.loads((SOURCE/'unity_build/prototype_verification.json').read_text())
    assert proof['status']=='passed'
    windows=SOURCE/'unity_build/map-shadowfall.prototype.unity3d'
    android=SOURCE/'unity_build/android/map-shadowfall.prototype.unity3d'
    assert proof['bundle_sha256']==sha(windows)
    assert json.loads((android.parent/'build_contract.json').read_text())['status']=='passed'
    files={}
    for platform,binary in (('windows',windows),('android',android)):
        manifest = binary.with_name(binary.name+'.manifest').read_text()
        crc = re.search(r'^CRC: (\d+)$',manifest,re.M)
        assert crc
        files[platform]=dict(sha256=sha(binary),crc=int(crc[1]),bytes=binary.stat().st_size)
    bundle=dict(ID=2100000,Name='shadowtest',Type='MAP',FileName='maps/custom/2100000_map-shadowtest.unity3d',
                Version=1,VersionContent=1,VersionStage=1,VersionLive=1,DependencyID=0,
                CRCPC=str(files['windows']['crc']),CRCAndroid=str(files['android']['crc']),_cdnvL=1)
    inline={k:v for k,v in bundle.items() if k not in ('FileName','_cdnvL')}
    inline['Filename']=bundle['FileName']
    area=dict(Cmd='AreaJoin',areaName='shadowtest-1',strMapName='shadowtest',DisplayName='Shadowfall Test',
              Bundle=inline,PrefabName='map-shadowfall',intType=1,sExtra='',areaId=2100000,Frame='Enter',Pad='Spawn',
              monBranch=[],uoBranch=[],QuestIDs=[])
    doc=dict(area=area,cells={name:dict(Cmd='CellJoin',Frame=name,Pad=pad,entities=[],monsterSpawns=[])
                            for name,pad in (('Enter','Spawn'),('Garden1','Bottom'))})
    payload=dict(map_name='shadowtest',map_id=2100000,bundle_id=2100000,bundle=bundle,doc=doc,files=files,
                 preflight={k:preflight[k] for k in ('registry_sha256','services','engines','server_hashes')},
                 prototype_proof=proof,reason='User explicitly requested publishing the current prototype to the live server under map name shadowtest.')
    (OUT/'deployment_payload.json').write_text(json.dumps(payload,indent=2))
    print(json.dumps(dict(status='prepared',map='shadowtest',map_id=2100000,bundle_id=2100000,files=files),indent=2))


def stage():
    stream=io.BytesIO()
    with tarfile.open(fileobj=stream,mode='w') as archive:
        for name,path in {
            'deployment_payload.json':OUT/'deployment_payload.json',
            'shadowtest_live.py':ROOT/'scripts/swf2unity/shadowtest_live.py',
            'windows.unity3d':SOURCE/'unity_build/map-shadowfall.prototype.unity3d',
            'android.unity3d':SOURCE/'unity_build/android/map-shadowfall.prototype.unity3d',
        }.items():
            archive.add(path,arcname=name)
    host.remote('mkdir -p '+REMOTE+' && tar -xf - -C '+REMOTE,stream.getvalue())
    run('dry-run')


def run(mode):
    result=host.server_script(REMOTE+'/shadowtest_live.py',mode+' --payload '+REMOTE+'/deployment_payload.json',timeout=120)
    parsed=json.loads(result)
    (OUT/(mode.replace('-','_')+'_result.json')).write_bytes(result)
    assert parsed['status']==('preflight_passed' if mode=='dry-run' else 'applied' if mode=='apply' else 'passed')
    print(result.decode())


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode',choices=('prepare','stage','dry-run','apply','verify'))
    args=parser.parse_args()
    OUT.mkdir(parents=True,exist_ok=True)
    if args.mode=='prepare': prepare()
    elif args.mode=='stage': stage()
    else: run(args.mode)
