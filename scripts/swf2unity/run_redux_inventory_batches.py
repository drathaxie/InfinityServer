"""Run a finite owned-inventory queue with the shared Unity lock and disk guards."""
from datetime import datetime,timezone
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import native_batches as n
from trial_host import ROOT


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--start',type=int,default=32);args=ap.parse_args()
    assert 32<=args.start<=43
    folder=ROOT/'outputs/flash_converter_trial/redux_inventory/worker';folder.mkdir(exist_ok=True)
    lock=n.BASE/'specialty_worker/worker.lock'
    with lock.open('x') as f:f.write(str(os.getpid()))
    jobs=[('specialty',32,16),('armor',33,6),('specialty',34,16),('armor',35,6),('specialty',36,16),('armor',37,6),('specialty',38,16),('armor',39,6),('armor',40,6),('armor',41,6),('armor',42,6),('armor',43,6)]
    jobs=[job for job in jobs if job[1]>=args.start]
    prior=folder/'status.json'
    if prior.exists():
        shutil.copy2(prior,folder/('status-before-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'.json'))
    state=dict(pid=os.getpid(),started_utc=datetime.now(timezone.utc).isoformat(),scope='redux_inventory_only',status='running',queue=[dict(kind=k,batch=f'native{b:02}',size=s) for k,b,s in jobs],results=[])
    def save():n.write(folder/'status.json',state)
    try:
        for kind,bid,size in jobs:
            batch=f'native{bid:02}';state.update(current=batch,kind=kind);save()
            complete=n.BASE/batch/'live_verification.json'
            if complete.exists():
                proof=json.loads(complete.read_text());assert proof['status']=='passed'
                pipeline=n.BASE/batch/'pipeline_status.json'
                progress=json.loads(pipeline.read_text()) if pipeline.exists() else {}
                if progress.get('status')!='complete' or progress.get('phase')!='grant':
                    n.pipeline(n.BASE/batch,size,universal=True)
                state['results'].append(dict(batch=batch,status='already_verified',bundles=len(proof['entries'])));save();continue
            assert shutil.disk_usage(ROOT).free>12*1024**3,'Below 12 GiB free; preserve sources and stop before new render work'
            cmd=([sys.executable,'-u',str(ROOT/'scripts/swf2unity/specialty_batches.py'),'run','--batch',batch,'--size',str(size)] if kind=='specialty' else [sys.executable,'-u',str(ROOT/'scripts/swf2unity/inventory_armor_batch.py'),'--batch',batch,'--size',str(size)])
            with (folder/(batch+'.log')).open('w',encoding='utf-8') as log:
                result=subprocess.run(cmd,cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,creationflags=subprocess.CREATE_NO_WINDOW,timeout=43200)
            complete=n.BASE/batch/'live_verification.json'
            if result.returncode:
                # Record precise source/tool cases; publication failures stop the
                # queue, while source-only failures leave other categories useful.
                if (n.BASE/batch/'deployment_payload.json').exists():raise RuntimeError(batch+' publication failed; inspect its log')
                state['results'].append(dict(batch=batch,status='needs_compiler_work'));save();continue
            proof=json.loads(complete.read_text());assert proof['status']=='passed'
            state['results'].append(dict(batch=batch,status='verified',bundles=len(proof['entries'])));save()
        state['status']='queue_finished';save()
    except Exception as error:
        state.update(status='failed',error=str(error));save();raise
    finally:
        if lock.exists() and lock.read_text()==str(os.getpid()):lock.unlink()


if __name__=='__main__':main()
