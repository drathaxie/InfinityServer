"""Run a bounded sequence of guarded specialty batches in one hidden worker."""
import argparse
import datetime
import json
import os
from pathlib import Path
import subprocess
import shutil
import sys
import time

from trial_host import ROOT
import native_batches as n


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--start', type=int, required=True)
    parser.add_argument('--sizes', default='36,54,64')
    parser.add_argument('--after', default='native13')
    parser.add_argument('--worker-name', default='specialty_worker')
    parser.add_argument('--wait-hours', type=int, default=3)
    parser.add_argument('--defer-lock', action='store_true', help='Wait for a predecessor before acquiring the shared Unity worker lock')
    args = parser.parse_args()
    sizes = [int(size) for size in args.sizes.split(',')]
    assert sizes and all(1 <= size <= 64 for size in sizes)
    assert 1 <= args.start <= 99 - len(sizes) + 1
    assert n.re.fullmatch(r'native\d{2}', args.after)
    assert n.re.fullmatch(r'[a-z][a-z0-9_]{0,40}', args.worker_name) and 1 <= args.wait_hours <= 48
    folder = n.BASE / args.worker_name; folder.mkdir(exist_ok=True)
    lock = n.BASE / 'specialty_worker/worker.lock'
    state = dict(pid=os.getpid(), started_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                 status='waiting', after=args.after, queue=[dict(batch=f'native{args.start+i:02}', size=size)
                                                           for i, size in enumerate(sizes)], results=[])
    owns_lock = False
    def acquire():
        nonlocal owns_lock
        with lock.open('x') as handle:
            handle.write(str(os.getpid()))
        owns_lock = True
    if not args.defer_lock:
        acquire()
    def save(**updates):
        state.update(updates)
        n.write(folder/'status.json', state)
        print(json.dumps({k: state[k] for k in ('status', 'current') if k in state}), flush=True)
    try:
        save()
        deadline = time.monotonic() + args.wait_hours * 3600
        while True:
            previous = n.BASE / args.after / 'pipeline_status.json'
            prior = json.loads(previous.read_text()) if previous.exists() else {}
            if prior.get('status') == 'complete':
                verification = json.loads((previous.parent/'live_verification.json').read_text())
                assert verification['status'] == 'passed', verification['status']
                break
            assert prior.get('status') != 'failed', f"Preceding batch failed: {prior}"
            assert time.monotonic() < deadline, 'Preceding batch exceeded configured wait window'
            time.sleep(15)
        if not owns_lock:
            # A predecessor writes its final batch marker just before releasing
            # the lock. Give that final bookkeeping a bounded interval.
            for attempt in range(12):
                if not lock.exists():break
                time.sleep(5)
            acquire()
        for job in state['queue']:
            assert shutil.disk_usage(ROOT).free > 12 * 1024**3, 'Less than 12 GiB free; batch stopped before preparing more render caches'
            save(status='running', current=job['batch'])
            log = folder / (job['batch'] + '.log')
            with log.open('w', encoding='utf-8') as output:
                result = subprocess.run([sys.executable, str(Path(__file__).with_name('specialty_batches.py')),
                    'run', '--batch', job['batch'], '--size', str(job['size'])], cwd=ROOT,
                    stdout=output, stderr=subprocess.STDOUT, creationflags=subprocess.CREATE_NO_WINDOW,
                    timeout=43200)
            assert result.returncode == 0, f"{job['batch']} failed; see {log}"
            verification = json.loads((n.BASE/job['batch']/'live_verification.json').read_text())
            assert verification['status'] == 'passed', verification['status']
            state['results'].append(dict(batch=job['batch'], bundles=len(verification['entries']), log=str(log)))
            save()
        save(status='complete')
    except Exception as error:
        save(status='failed', error=str(error))
        raise
    finally:
        if owns_lock:
            lock.unlink()


if __name__ == '__main__':
    main()
