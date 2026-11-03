"""Add late source-frame evidence to immutable, already-published native sword builds."""
import json
from pathlib import Path
import weapon
from trial_host import ROOT

BASE = ROOT / 'outputs/flash_converter_trial/native_replacements'


def main():
    checks = []
    for batch in sorted(BASE.glob('native[0-9][0-9]')):
        if batch.name == 'native01' or not (batch / 'live_verification.json').exists():
            continue
        for entry in json.loads((batch / 'build_plan.json').read_text()):
            conversion = Path(entry['conversion'])
            data = json.loads((conversion / 'armor.json').read_text())
            periods = {layer['period'] for gender in data['genders'].values()
                       for renderer in gender['renderers'].values() for layer in renderer.get('layers') or []}
            samples = sorted({f for period in periods if period > 1
                              for f in (period - 1, period, 2 * period - 1, 2 * period)})
            if not samples:
                continue
            check = dict(batch=batch.name, bundle_id=entry['native_bundle_id'], name=entry['name'],
                         bundle_sha256=entry['bundle_sha256'], samples=samples)
            print('LATE_CHECK', check['bundle_id'], check['name'], samples, flush=True)
            try:
                result = weapon.validate(str(conversion), max(samples) + 1, samples=samples,
                                         report_name='late_boundary_validation')
                check.update(status='passed' if result['worst_mean'] <= 25
                             and result['max_clipped_alpha'] <= 0.5 else 'failed', result=result)
            except Exception as error:
                check.update(status='failed', error=str(error))
            checks.append(check)
            (BASE / 'late_boundary_audit.json').write_text(json.dumps(dict(status='running', checks=checks), indent=2))
    passed = all(c['status'] == 'passed' for c in checks)
    (BASE / 'late_boundary_audit.json').write_text(json.dumps(dict(status='passed' if passed else 'failed', checks=checks), indent=2))
    print('LATE_AUDIT', 'passed' if passed else 'failed', len(checks), 'animated native swords', flush=True)
    if not passed:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
