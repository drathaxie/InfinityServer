"""Reuse source-validated conversions held only by a Unity compiler failure."""
import argparse
import json
from pathlib import Path
import native_batches as n


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--from-batch',required=True);ap.add_argument('--batch',required=True);args=ap.parse_args()
    assert all(n.re.fullmatch(r'native\d{2}',x) for x in (args.from_batch,args.batch))
    source=n.BASE/args.from_batch;target=n.BASE/args.batch
    assert (source/'live_verification.json').is_file()
    assert not(target/'prepared_plan.json').exists() and not(target/'deployment_result.json').exists()
    assert not(n.BASE/'specialty_worker/worker.lock').exists()
    held={r['bundle_id'] for r in json.loads((source/'held_candidates.json').read_text()) if 'FlashRig shader failed compilation or is unsupported' in r['reason']}
    used={e['bundle_id'] for path in n.BASE.glob('native*/live_verification.json') for e in json.loads(path.read_text())['entries']}
    target.mkdir(exist_ok=True);entries=[]
    for old in json.loads((source/'prepared_plan.json').read_text()):
        bid=old['native_bundle_id']
        if bid not in held or bid in used:continue
        conversion=Path(old['conversion']);check=json.loads((conversion/'validation.json').read_text())
        assert check['worst_mean']<=25 and check['max_clipped_alpha']<=.5
        entry=dict(old);output=target/old['key']/'bundle';output.parent.mkdir(exist_ok=True)
        entry['bundle']=str(output/old['bundle_name'])
        n.run('build_armor.py',output.parent,conversion,'-','--bundle',old['bundle_name'],'--project',n.PROJECT,
              '--output-dir',output,'--validate',','.join(map(str,old['frames'])),'--prepare-only')
        actual=json.loads(Path(entry['unity_input']).read_text())
        assert Path(actual['outputDir']).resolve()==output.resolve()
        entries.append(entry);n.write(target/'prepared_plan.json',entries)
        print('SOURCE_VALIDATED_RETRY',bid,entry['name'],flush=True)
    assert entries
    n.write(target/'compiler_retry_manifest.json',dict(source_batch=args.from_batch,status='prepared',bundles=[e['native_bundle_id'] for e in entries],shader_compiler_timeout_minutes=20))


if __name__=='__main__':main()
