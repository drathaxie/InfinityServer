"""Audit, stage, publish and publicly verify the existing armor skin repair."""
import argparse
from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
import tarfile
import UnityPy
from trial_host import ROOT, remote, server_script

BASE=ROOT/'outputs/flash_converter_trial/skin_fix'
REMOTE='/tmp/infinity-armor-skin-fix-20261012'
IDS='2000002 2000005'
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def stage():
    assert not (BASE/'deployment_result.json').exists(), 'Published repair is immutable'
    plan=json.loads((BASE/'repair_plan.json').read_text());assert len(plan)==2
    if BASE.name=='body_limb_fix':
        assert json.loads((BASE/'regression_checks.json').read_text())['status']=='passed'
        for entry in plan:
            source=json.loads((Path(entry['conversion'])/'validation.json').read_text())
            assert source and all(v['frames']>0 and v['worst_frame_mean']<=25 and v['clipped']<=.5 for v in source.values())
    entries=[]
    for e in plan:
        binary=Path(e['bundle']);folder=binary.parent
        gpu=json.loads((folder/'unity_validation_summary.json').read_text())
        contract=json.loads((folder/'bundle_contract.json').read_text())
        assert gpu['status']==contract['status']=='passed' and gpu['expected_captures']==gpu['compared_captures']>0
        env=UnityPy.load(str(binary));heads=[]
        for obj in env.objects:
            if obj.type.name=='MonoBehaviour' and obj.read().m_Script.read().m_ClassName=='ItemSlots':
                head=next(s for s in obj.read_typetree()['slots'] if s['slot']==23)
                assert head['CCMask']['m_PathID']
                mask=next(o for o in env.objects if o.path_id==head['CCMask']['m_PathID']).read().image.convert('RGB')
                import numpy as np
                values=np.asarray(mask)
                assert np.count_nonzero(values[...,0]>160)>20, 'skin channel absent'
                heads.append(head['CCMask']['m_PathID'])
        assert len(heads)==2 and len(set(heads))==2
        entry=dict(id=e['item_id'],sha256=sha(binary),crc=contract['bundleCRC'])
        entries.append(entry)
        (folder/'skin_bundle_audit.json').write_text(json.dumps(dict(status='passed',heads=heads,gpu=gpu),indent=2))
    before=json.loads(server_script('-', '--inspect --ids '+IDS, data=(ROOT/'scripts/swf2unity/deploy_skin_fix.py').read_bytes()))
    for e,old in zip(entries,before['entries']):
        assert e['id']==old['id']
        version=max(int(old['bundle'].get(k,0)) for k in ('Version','VersionContent','VersionStage','VersionLive'))+1
        bundle=deepcopy(old['bundle']);bundle.update({k:version for k in ('Version','VersionContent','VersionStage','VersionLive','_cdnvL')});bundle['CRCPC']=str(e['crc'])
        relative=Path(bundle['FileName'])
        e.update(version=version,bundle=bundle,target=(Path('/opt/infinity/content/game/assetbundles/windows')/relative.parent/relative.stem/str(version)/relative.name).as_posix())
    (BASE/'payload.json').write_text(json.dumps(dict(before=before,entries=entries),indent=2))
    stream=io.BytesIO()
    with tarfile.open(fileobj=stream,mode='w') as archive:
        for path in [BASE/'payload.json']+[Path(e['bundle']) for e in plan]+[ROOT/'scripts/swf2unity'/n for n in ('deploy_skin_fix.py','deploy_pet_trial.py')]:
            archive.add(path,arcname=path.name)
    remote('mkdir -p '+REMOTE+' && tar -xf - -C '+REMOTE,stream.getvalue())
    result=json.loads(server_script(REMOTE+'/deploy_skin_fix.py','--payload '+REMOTE+'/payload.json --ids '+IDS))
    assert result['status']=='preflight_passed';(BASE/'dry_run.json').write_text(json.dumps(result,indent=2));print(result)


def apply():
    assert not (BASE/'deployment_result.json').exists()
    assert json.loads((BASE/'dry_run.json').read_text())['status']=='preflight_passed'
    result=json.loads(server_script(REMOTE+'/deploy_skin_fix.py','--payload '+REMOTE+'/payload.json --apply --ids '+IDS))
    (BASE/'deployment_result.json').write_text(json.dumps(result,indent=2));print(json.dumps(result))


def verify():
    script='''
import json,hashlib,urllib.request,db,game
p=json.load(open("'''+REMOTE+'''/payload.json"))
c=db.connect()
try:
 inventory={i['ID']:i for i in game.inventory(c,8)}
 with urllib.request.urlopen('https://divinityarts.mooo.com/data/GetAssetBundlesByIDs?ids=' + ','.join(str(e['id']) for e in p['entries'])) as r: bundles={b['ID']:b for b in json.load(r)}
 for e in p['entries']:
  with urllib.request.urlopen('https://content.130-162-189-229.sslip.io/'+e['target'].split('/content/')[1]) as r: assert hashlib.sha256(r.read()).hexdigest()==e['sha256']
  assert bundles[e['id']]['VersionLive']==e['version'] and bundles[e['id']]['CRCPC']==str(e['crc'])
  assert inventory[e['id']]['Bundle']['VersionLive']==e['version']
 print(json.dumps(dict(status='passed',ids=[e['id'] for e in p['entries']],public_hashes=True,inventory_versions=True)))
finally:c.close()
'''
    result=json.loads(server_script('-',data=script.encode()));(BASE/'live_verification.json').write_text(json.dumps(result,indent=2));print(result)


if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('mode',choices=['stage','apply','verify'])
    ap.add_argument('--repair',choices=['skin_fix','body_limb_fix'],default='skin_fix')
    args=ap.parse_args()
    if args.repair=='body_limb_fix':
        BASE=ROOT/'outputs/flash_converter_trial/body_limb_fix'
        REMOTE='/tmp/infinity-armor-body-limb-fix-20261012'
        IDS='2000005 2000007'
    globals()[args.mode]()
