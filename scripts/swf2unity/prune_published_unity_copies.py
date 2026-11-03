"""Remove regenerable Unity asset copies for verified published native batches."""
import argparse
from datetime import datetime,timezone
import json
import os
from pathlib import Path
import shutil
import subprocess

from trial_host import ROOT
import native_batches as n


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--apply',action='store_true');args=ap.parse_args()
    project=n.PROJECT.resolve();base=(project/'Assets/Armors').resolve()
    assert project.is_relative_to(ROOT.resolve()) and base.is_relative_to(project)
    # The asset pipeline must be idle before touching its disposable inputs.
    check=subprocess.run(['tasklist','/FI','IMAGENAME eq Unity.exe','/FO','CSV','/NH'],capture_output=True,text=True,check=True)
    assert '"Unity.exe"' not in check.stdout,'Unity is running'
    assert not(n.BASE/'specialty_worker/worker.lock').exists(),'Conversion worker owns the project'
    targets={}
    for batch in sorted(n.BASE.glob('native[0-9][0-9]')):
        proof=batch/'live_verification.json';plan=batch/'build_plan.json'
        if not(proof.is_file() and plan.is_file()):continue
        verification=json.loads(proof.read_text())
        if verification.get('status')!='passed':continue
        ids={e['bundle_id'] for e in verification['entries']}
        for entry in json.loads(plan.read_text()):
            if entry['native_bundle_id'] not in ids or 'unity_input' not in entry:continue
            path=Path(entry['unity_input']).resolve().parent
            if path.parent!=base:continue  # Older plans point at retained conversion evidence.
            assert not path.is_symlink(),str(path)
            if not path.is_dir():continue
            assert (Path(entry['conversion'])/'armor.json').is_file()
            assert Path(entry['bundle']).is_file()
            validation=Path(entry['bundle']).parent/'build_validation.json'
            assert json.loads(validation.read_text())['status']=='passed'
            targets[path]=dict(batch=batch.name,bundle_id=entry['native_bundle_id'])
    report=dict(status='planned',utc=datetime.now(timezone.utc).isoformat(),project=str(project),directories=[],bytes=0)
    for path,info in targets.items():
        files=[]
        for folder,dirs,names in os.walk(path,followlinks=False):
            assert all(not(Path(folder)/d).is_symlink() for d in dirs)
            for name in names:
                f=Path(folder)/name;assert not f.is_symlink();files.append(f)
        size=sum(f.stat().st_size for f in files)
        report['directories'].append(dict(path=str(path),files=len(files),bytes=size,**info));report['bytes']+=size
    destination=ROOT/'outputs/battlepet_forge_lab/live/published_unity_cache_cleanup.json'
    destination.write_text(json.dumps(report,indent=2))
    if args.apply:
        backup=destination.parent/'unity-input-backups';backup.mkdir(exist_ok=True)
        for path in targets:
            # Preserve the tiny reproducibility manifest; atlases/animations are
            # generated again from the retained conversion JSON/source evidence.
            manifest=path/'unity_input.json'
            if manifest.exists():shutil.copy2(manifest,backup/(path.name+'.json'))
            assert path.resolve().parent==base and not path.is_symlink()
            shutil.rmtree(path)
            meta=path.with_name(path.name+'.meta')
            if meta.is_file():meta.unlink()
        report.update(status='removed_generated_copies',free_gib=shutil.disk_usage(ROOT).free/1024**3)
        destination.write_text(json.dumps(report,indent=2))
    print(json.dumps({k:v for k,v in report.items() if k!='directories'}|{'directories':len(targets)},indent=2))


if __name__=='__main__':main()
