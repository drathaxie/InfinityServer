"""Reclaim only regenerable native Unity inputs; retain all sources/build evidence."""
import argparse
from datetime import datetime,timezone
import json
import os
from pathlib import Path
import shutil
import subprocess
import native_batches as n
from trial_host import ROOT


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--apply',action='store_true');ap.add_argument('--worker-pid',type=int);args=ap.parse_args()
    root=ROOT.resolve();base=(n.PROJECT/'Assets/Armors').resolve();assert base.is_relative_to(root)
    procs=subprocess.check_output(['tasklist','/FI','IMAGENAME eq Unity.exe','/FO','CSV','/NH'],text=True)
    assert '"Unity.exe"' not in procs, 'Unity must be stopped before pruning generated inputs'
    lock=n.BASE/'specialty_worker/worker.lock'
    if lock.exists():
        assert args.worker_pid and lock.read_text()==str(args.worker_pid)
        # A child batch can reclaim copies between Unity runs only when this
        # exact inventory worker is an actual ancestor of the cleaner.
        ancestor=os.getppid();found=False
        for _ in range(5):
            query=f"Get-CimInstance Win32_Process -Filter 'ProcessId={ancestor}' | Select-Object ProcessId,ParentProcessId,CommandLine | ConvertTo-Json -Compress"
            info=json.loads(subprocess.check_output(['powershell.exe','-NoProfile','-Command',query],text=True))
            if ancestor==args.worker_pid:
                assert 'run_redux_inventory_batches.py' in info['CommandLine'];found=True;break
            ancestor=int(info['ParentProcessId'])
        assert found, 'Cleanup caller is not a child of the locked inventory worker'
    else:assert args.worker_pid is None
    candidates={}
    for name in ('prepared_plan.json','build_plan.json'):
        for plan in n.BASE.glob('native*/'+name):
            for e in json.loads(plan.read_text()):
                if 'unity_input' not in e:continue
                path=Path(e['unity_input']).resolve().parent;conversion=Path(e['conversion']).resolve()
                if path.parent!=base or not path.is_dir() or path.is_symlink():continue
                assert conversion.is_relative_to(root) and not conversion.is_relative_to(n.PROJECT.resolve())
                if 'caladbolg' in (str(path)+' '+e['name']).lower():continue
                data=json.loads((conversion/'armor.json').read_text())
                assert all((conversion/g['atlas']).is_file() for g in data['genders'].values())
                inp=json.loads((path/'unity_input.json').read_text());assert inp['name']==path.name and inp['folder']=='Assets/Armors/'+path.name
                files=list(path.rglob('*'));assert not any(f.is_symlink() for f in files)
                assert not any(f.suffix.lower() in ('.swf','.fla','.cs','.py') for f in files)
                candidates[path]=dict(conversion=str(conversion),bytes=sum(f.stat().st_size for f in files if f.is_file()))
    report=dict(utc=datetime.now(timezone.utc).isoformat(),status='planned',bytes=sum(x['bytes'] for x in candidates.values()),directories=[dict(path=str(p),**v) for p,v in candidates.items()])
    backup=ROOT/'outputs/flash_converter_trial/redux_inventory/generated_input_backups';backup.mkdir(exist_ok=True)
    if args.apply:
        for path in candidates:
            assert path.resolve().parent==base and not path.is_symlink()
            shutil.copy2(path/'unity_input.json',backup/(path.name+'-'+n.sha(path/'unity_input.json')[:12]+'.json'));shutil.rmtree(path)
            meta=path.with_name(path.name+'.meta')
            if meta.is_file():meta.unlink()
        report.update(status='removed_regenerable_inputs',free_gib=shutil.disk_usage(ROOT).free/1024**3)
    (backup/'cleanup.json').write_text(json.dumps(report,indent=2))
    (backup/('cleanup-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'.json')).write_text(json.dumps(report,indent=2))
    print(json.dumps({k:v for k,v in report.items() if k!='directories'}|{'directories':len(candidates)},indent=2))


if __name__=='__main__':main()
