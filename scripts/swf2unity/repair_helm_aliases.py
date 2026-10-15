"""Add native HelmGO lookup aliases without changing any rendered asset object."""
import copy
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess

import UnityPy
import build_armor
import native_batches as n
from trial_host import ROOT

LAB=ROOT/'outputs/flash_converter_trial/helm_visibility_repair'
BATCH=n.BASE/'native31'
PROJECT=LAB/'UnityProject'


def objects(env):
    return {o.path_id:hashlib.sha256(o.get_raw_data()).hexdigest() for o in env.objects if o.type.name!='AssetBundle'}


def prepare():
    assert not(BATCH/'deployment_result.json').exists()
    BATCH.mkdir(exist_ok=True);entries=[];jobs=[];proofs=[]
    published={}
    for plan in sorted(n.BASE.glob('native*/build_plan.json')):
        if not(plan.parent/'live_verification.json').exists():continue
        for e in json.loads(plan.read_text()):
            if e['kind']=='helm':published[e['native_bundle_id']]=e
    assert published
    for index,old in enumerate(published.values()):
        folder=BATCH/old['key'];output=folder/'bundle';output.mkdir(parents=True,exist_ok=True)
        source=Path(old['bundle']);target=output/source.name
        env=UnityPy.load(str(source));before=objects(env)
        obj=next(o for o in env.objects if o.type.name=='AssetBundle');tree=obj.read_typetree()
        original=next((k,v) for k,v in tree['m_Container'] if k.endswith('/'+old['prefab'].lower()+'.prefab'))
        alias=str(Path(original[0]).with_name('helmgo.prefab')).replace('\\','/')
        assert not any(k==alias for k,v in tree['m_Container'])
        tree['m_Container'].append((alias,copy.deepcopy(original[1])));obj.save_typetree(tree)
        target.write_bytes(env.file.save(packer='lz4'))
        loaded=UnityPy.load(str(target));assert objects(loaded)==before,'Rendered serialized objects changed'
        assert alias in loaded.container and original[0] in loaded.container
        assert loaded.container[alias].path_id==loaded.container[original[0]].path_id
        proof=dict(bundle_id=old['native_bundle_id'],old_sha256=n.sha(source),new_sha256=n.sha(target),unchanged_render_objects=len(before),alias=alias)
        proofs.append(proof)
        shutil.copy2(source.parent/'bundle_contract.json',output/'bundle_contract.json')
        shutil.copy2(source.with_suffix('.unity3d.manifest'),target.with_suffix('.unity3d.manifest'))
        n.run('build_armor.py',folder,old['conversion'],'-','--bundle',source.name,'--project',PROJECT,
              '--output-dir',output,'--validate',','.join(map(str,old['frames'])),'--prepare-only')
        inp=PROJECT/'Assets/Armors'/old['key']/'unity_input.json';data=json.loads(inp.read_text())
        data['existingBundle']=str(target);inp.write_text(json.dumps(data));jobs.append(str(inp))
        entry=dict(old,bundle=str(target),unity_input=str(inp));entries.append(entry)
        if index==0:
            negative=copy.deepcopy(data);negfolder=LAB/'negative_control';negfolder.mkdir(exist_ok=True)
            negative.update(existingBundle=str(source),expectMissingHelmGO=True,outputDir=str(negfolder))
            neg=LAB/'negative_control_input.json';neg.write_text(json.dumps(negative));jobs.insert(0,str(neg))
        print('ALIAS_PREPARED',old['native_bundle_id'],old['name'],flush=True)
    n.write(BATCH/'prepared_plan.json',entries);n.write(BATCH/'held_candidates.json',[])
    n.write(LAB/'serialized_object_preservation.json',proofs)
    n.write(LAB/'validation_batch.json',dict(inputs=jobs))
    # The validation project is independent of the running conversion worker.
    (PROJECT/'ProjectSettings').mkdir(exist_ok=True)
    (PROJECT/'ProjectSettings/ProjectVersion.txt').write_text('m_EditorVersion: 6000.3.18f1\n')


def validate():
    run=subprocess.run([build_armor.UNITY,'-batchmode','-quit','-projectPath',str(PROJECT),'-executeMethod',
        'ArmorBuilder.ValidateHelmMany','-armorBatchInput',str(LAB/'validation_batch.json'),'-logFile',str(LAB/'unity_alias_validation.log')],
        creationflags=subprocess.CREATE_NO_WINDOW,env=build_armor.unity_environment(),timeout=3600)
    assert run.returncode==0,'Native alias validation failed; inspect isolated Unity log'
    entries=json.loads((BATCH/'prepared_plan.json').read_text())
    assert json.loads((LAB/'negative_control/negative_control.json').read_text())['status']=='passed'
    for e in entries:
        bundle=Path(e['bundle']);report=json.loads((bundle.parent/'bundle_contract.json').read_text());assert report['helmAttachmentContract']==2
        manifest=bundle.with_suffix('.unity3d.manifest');text=manifest.read_text();text,count=re.subn(r'^CRC: \d+$','CRC: '+str(report['bundleCRC']),text,count=1,flags=re.MULTILINE);assert count==1;manifest.write_text(text)
    n.finalize(BATCH,entries,[],len(entries))
    from specialty_batches import validate_selectors
    validate_selectors(BATCH,len(entries))
    n.write(LAB/'repair_validation.json',dict(status='passed',bundles=len(entries),native_prefab='HelmGO',negative_control=True,rendered_objects_unchanged=True))


if __name__=='__main__':
    if not(BATCH/'prepared_plan.json').exists():prepare()
    else:build_armor.sync_project(str(PROJECT))
    validate();n.pipeline(BATCH,len(json.loads((BATCH/'prepared_plan.json').read_text())),universal=True)
