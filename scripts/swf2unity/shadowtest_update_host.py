"""Prepare and publish verified revisions of the existing shadowtest only."""
import argparse
import copy
import io
import json
from pathlib import Path
import re
import tarfile

import trial_host as host
from shadowtest_host import sha

ROOT=Path(__file__).resolve().parents[2]
SOURCE=ROOT/'outputs/map_converter_trial/shadowfall/revisions/v2'
OUT=SOURCE/'live_shadowtest'
REMOTE='/tmp/infinity-shadowtest-v2-20261102'
VERSION=2


def preflight():
    result=host.server_script('-',data=(ROOT/'scripts/swf2unity/shadowtest_update_live.py').read_bytes())
    (OUT/'preflight.json').write_bytes(result)
    before=json.loads(result)
    print(json.dumps(dict(status='preflight_passed',version=before['bundle']['VersionLive'],services=before['services']),indent=2))


def prepare():
    before=json.loads((OUT/'preflight.json').read_text())
    proof=json.loads((SOURCE/'unity_build/prototype_verification.json').read_text())
    assert proof['status']=='passed' and proof['render']['status']=='passed'
    visuals=json.loads((SOURCE/'visuals/visuals.json').read_text())
    expected_frames=sum(l['period']>1 for c in visuals['cells'] for l in c['layers'])*len(visuals['samples'])
    assert proof['loaded_animator_checks']['passed']==expected_frames
    if VERSION>=3:
        expected_depth=sum(g['kind'] in ('depth','actor') for c in visuals['cells'] for g in c['groups'])
        assert len([c for c in proof['presentation_checks'] if c['kind']=='depth_front_behind'])==expected_depth
    if VERSION>=4:
        assert proof['raster_quality']['source_raster_scale']==3
        if VERSION<=5:assert len([c for c in proof['presentation_checks'] if c['kind']=='npc_dragon_overlap_exercised'])==1
    if VERSION>=6:
        kinds={c['kind'] for c in proof['effect_checks']}
        assert {'effect_fire_count','effect_fire_live','effect_fire_fit','effect_smoke_live','effect_bundle_binding'}<=kinds
        assert all(c['passed'] for c in proof['effect_checks'])
    if VERSION>=5:
        assert proof['raster_quality']['tiling']['unique_tiles']>0
        assert {t['format'] for t in proof['raster_quality']['textures']}=={'BC7'}
    platform_proof=json.loads((SOURCE/'unity_build/android/platform_verification.json').read_text())
    assert platform_proof['status']=='passed'
    if VERSION>=5:assert {t['format'] for t in platform_proof['android_textures'].values()}=={'ASTC_RGB_6x6'}  # UnityPy's name for Unity 6 ASTC_6x6 (alpha-capable)
    files={}
    for platform in ('windows','android'):
        directory=SOURCE/'unity_build'
        if platform=='android':directory=directory/'android'
        binary=directory/'map-shadowfall.prototype.unity3d'
        assert platform_proof[platform+'_sha256']==sha(binary)
        if platform=='windows':assert proof['bundle_sha256']==sha(binary)
        else:assert json.loads((directory/'build_contract.json').read_text())['status']=='passed'
        crc=re.search(r'^CRC: (\d+)$',binary.with_name(binary.name+'.manifest').read_text(),re.M)
        assert crc
        files[platform]=dict(sha256=sha(binary),bytes=binary.stat().st_size,crc=int(crc[1]))
    bundle=copy.deepcopy(before['bundle'])
    for key in ('Version','VersionContent','VersionStage','VersionLive','_cdnvL'):bundle[key]=VERSION
    bundle.update(CRCPC=str(files['windows']['crc']),CRCAndroid=str(files['android']['crc']))
    doc=json.loads(before['map']['doc'])
    inline=doc['area']['Bundle']
    for key in ('Version','VersionContent','VersionStage','VersionLive'):inline[key]=VERSION
    inline.update(CRCPC=bundle['CRCPC'],CRCAndroid=bundle['CRCAndroid'])
    payload=dict(map_id=2100000,bundle_id=2100000,version=VERSION,before=before,bundle=bundle,doc=doc,files=files,
                 prototype_proof=proof,reason='Continue the user-authorized shadowtest correction of scale, raster quality, native parallax and NPC/scenery depth sorting.' if VERSION>=3 else 'Continue the user-authorized shadowtest deployment with calibrated movement and source visual animation.')
    (OUT/'deployment_payload.json').write_text(json.dumps(payload,indent=2))
    print(json.dumps(dict(status='prepared',version=VERSION,files=files),indent=2))


def stage():
    stream=io.BytesIO()
    with tarfile.open(fileobj=stream,mode='w') as archive:
        for name,path in {
            'deployment_payload.json':OUT/'deployment_payload.json',
            'shadowtest_update_live.py':ROOT/'scripts/swf2unity/shadowtest_update_live.py',
            'windows.unity3d':SOURCE/'unity_build/map-shadowfall.prototype.unity3d',
            'android.unity3d':SOURCE/'unity_build/android/map-shadowfall.prototype.unity3d',
        }.items():archive.add(path,arcname=name)
    host.remote('mkdir -p '+REMOTE+' && tar -xf - -C '+REMOTE,stream.getvalue())
    run('dry-run')


def run(mode):
    result=host.server_script(REMOTE+'/shadowtest_update_live.py',mode+' --payload '+REMOTE+'/deployment_payload.json',timeout=120)
    parsed=json.loads(result)
    (OUT/(mode.replace('-','_')+'_result.json')).write_bytes(result)
    assert parsed['status']==('preflight_passed' if mode=='dry-run' else 'applied' if mode=='apply' else 'passed')
    print(result.decode())


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode',choices=('preflight','prepare','stage','dry-run','apply','verify'))
    parser.add_argument('--revision',type=int,choices=(2,3,4,5,6,7),default=2)
    args=parser.parse_args();VERSION=args.revision
    SOURCE=ROOT/('outputs/map_converter_trial/shadowfall/revisions/v'+str(VERSION))
    OUT=SOURCE/'live_shadowtest';REMOTE='/tmp/infinity-shadowtest-v'+str(VERSION)+'-20261102'
    OUT.mkdir(parents=True,exist_ok=True)
    if args.mode=='preflight':preflight()
    elif args.mode=='prepare':prepare()
    elif args.mode=='stage':stage()
    else:run(args.mode)
