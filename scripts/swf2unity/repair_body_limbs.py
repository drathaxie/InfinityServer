"""Rebuild the two reported armor regressions in a new evidence namespace."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
from trial_host import ROOT

BASE = ROOT / 'outputs/flash_converter_trial/body_limb_fix'
TEMPLATE = ROOT / 'outputs/flash_converter_trial/skin_fix/template'

def main():
    BASE.mkdir(exist_ok=True)
    assert not (BASE/'deployment_result.json').exists(), 'Published repair evidence is immutable; use a new namespace'
    trials = [('ChronoStriker',2000005,'skin_fix/ChronoStriker','batch02/ChronoStriker/source','ChronoStriker.swf','chronostriker_batch02_flash_trial.unity3d'),
              ('ChronomancerPirate',2000007,'batch03/ChronomancerPirate','batch03/ChronomancerPirate/source','IPC.swf','chronomancerpirate_batch03_flash_trial.unity3d')]
    plan=[]
    for key,i,old,source,filename,bundle in trials:
        folder=BASE/key;out=folder/'conversion';out.mkdir(parents=True,exist_ok=True)
        oldwork=ROOT/'outputs/flash_converter_trial'/old/'conversion/work'
        if oldwork.exists() and not (out/'work').exists(): shutil.copytree(oldwork,out/'work')
        plan.append(dict(key=key,item_id=i,conversion=str(out),source=str(ROOT/'outputs/flash_converter_trial'/source),filename=filename,bundle=str(folder/'bundle'/bundle)))
    (BASE/'repair_plan.json').write_text(json.dumps(plan,indent=2))
    for e in plan:
        out=Path(e['conversion']);src=Path(e['source']);folder=out.parent
        commands=[['armor.py',str(src/'classes/M'/e['filename']),str(src/'classes/F'/e['filename']),str(TEMPLATE),str(out),'--name',e['key'],'--max-period','1200'],
                  ['validate_armor.py',str(out),str(src/'classes/M'/e['filename']),str(src/'classes/F'/e['filename']),'--frames','12','--sheet'],
                  ['build_armor.py',str(out),str(TEMPLATE),'--bundle',Path(e['bundle']).name,'--project',str(ROOT/'outputs/flash_converter_trial/FlyingMoglin/UnityProject'),'--output-dir',str(Path(e['bundle']).parent),'--validate','0,19,199,1099,1100,2199,2200']]
        for cmd in commands:
            print(e['key'],cmd[0],flush=True)
            r=subprocess.run([sys.executable,str(ROOT/'scripts/swf2unity'/cmd[0])]+cmd[1:],capture_output=True,text=True)
            (folder/(cmd[0]+'.log')).write_text(r.stdout+r.stderr)
            print(r.stdout[-1000:]+r.stderr[-1000:],flush=True)
            if r.returncode: raise RuntimeError(cmd[0]+' failed')

if __name__=='__main__':main()
