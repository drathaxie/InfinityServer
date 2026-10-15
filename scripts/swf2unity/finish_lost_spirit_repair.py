"""Finish the streaming regression and queue its source only after fidelity passes."""
import argparse
import ctypes
from ctypes import wintypes
import json
from pathlib import Path
import time

import native_batches as n


class Memory(ctypes.Structure):
    _fields_ = [('cb', wintypes.DWORD), ('faults', wintypes.DWORD)] + [
        (key, ctypes.c_size_t) for key in ('peak_rss', 'rss', 'peak_pool_paged', 'pool_paged',
            'peak_pool_nonpaged', 'pool_nonpaged', 'pagefile', 'peak_pagefile')]


def wait_and_measure(pid, folder):
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    psapi = ctypes.WinDLL('psapi', use_last_error=True)
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(Memory), wintypes.DWORD]
    handle = kernel.OpenProcess(0x1000 | 0x10, False, pid)
    peak = 0
    deadline = time.monotonic() + 14400
    if not handle:
        return peak
    try:
        while True:
            memory = Memory(); memory.cb = ctypes.sizeof(memory)
            if psapi.GetProcessMemoryInfo(handle, ctypes.byref(memory), memory.cb):
                peak = max(peak, memory.peak_rss)
                n.write(folder/'streaming_memory.json',dict(pid=pid,peak_rss_bytes=peak,
                    prior_observed_rss_bytes=11200978944,phase='streaming_source_regression'))
            code = wintypes.DWORD()
            if not kernel.GetExitCodeProcess(handle,ctypes.byref(code)) or code.value != 259:
                return peak
            assert time.monotonic() < deadline, 'Streaming regression exceeded four hours'
            time.sleep(15)
    finally:
        kernel.CloseHandle(handle)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--wait-pid', type=int, required=True)
    args = parser.parse_args()
    folder = n.BASE/'edge_repairs_20261013';status = folder/'lost_spirit_status.json'
    conversion = n.BASE/'native14/Specialty77407/conversion'
    try:
        n.write(status,dict(status='waiting',pid=args.wait_pid))
        peak = wait_and_measure(args.wait_pid, folder)
        check_path = conversion/'validation.json'
        if not check_path.exists():
            n.write(status,dict(status='retrying_cached_source'))
            n.run('weapon.py', folder, n.BASE/'specialty_sources/items/daggers/Dmnk01Legion26W05.swf',
                  'Dmnk01Legion26W05', conversion, '--prefab', 'dmnk01legion26w05_weapongo',
                  '--name','Exact77407','--expanded','--faithful-effects','--max-period','10000',
                  '--max-layers','128','--validate-samples','0,1,8735,8736,17471,17472,43679,43680,43681')
        check = json.loads(check_path.read_text())
        assert check['worst_mean']<=25 and check['max_clipped_alpha']<=.5,check
        row=next(r for r in json.loads((n.BASE/'native14/candidate_plan.json').read_text()) if r['template']['Bundle']['ID']==77407)
        source=n.BASE/'specialty_sources'/row['original']['File']
        assert n.sha(source)==row['source']['sha256']
        filename=Path(row['template']['Bundle']['Filename']).name
        model=json.loads((conversion/'armor.json').read_text());assert model['name']=='Exact77407'
        output=folder/'Exact77407/bundle'
        entry=dict(source_id=row['original']['id'],native_bundle_id=77407,key='Exact77407',kind='weapon',
            name=row['original']['Name'],prefab=row['template']['PrefabName'],original=row['original'],
            conversion=str(conversion),bundle=str(output/filename),bundle_name=filename,frames=check['samples'],
            sources=[dict(relative=row['original']['File'],path=str(source),**row['source'])],
            unity_input=str(n.PROJECT/'Assets/Armors/Exact77407/unity_input.json'))
        batch=n.BASE/'native16';batch.mkdir(exist_ok=True)
        assert not (batch/'pipeline_status.json').exists(), 'Native16 already started; avoid changing its in-memory plan'
        plan=json.loads((batch/'prepared_plan.json').read_text()) if (batch/'prepared_plan.json').exists() else []
        if not any(e['native_bundle_id']==77407 for e in plan):plan.append(entry)
        n.write(batch/'prepared_plan.json',plan)
        n.write(status,dict(status='source_passed_queued',batch='native16',worst_mean=check['worst_mean'],
            measured_peak_rss_bytes=peak,proof_boundary='Unity, serialized audit and guarded publication still required'))
    except Exception as error:
        n.write(status,dict(status='failed',error=str(error)))
        raise


if __name__=='__main__':main()
