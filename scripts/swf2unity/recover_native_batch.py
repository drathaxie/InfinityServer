"""Rebuild held outputs after a builder repair; prove capture isolation with an expected failure."""
import argparse
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import build_armor
import native_batches as native


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--batch', required=True)
    parser.add_argument('--flipbooks', action='store_true', help='Also rebuild accepted flipbooks with the closing-step guard')
    args = parser.parse_args()
    assert __import__('re').fullmatch(r'native\d{2}', args.batch)
    batch = native.BASE / args.batch
    assert not (batch / 'deployment_result.json').exists(), 'Published batches are immutable'
    original = json.loads((batch / 'prepared_plan.json').read_text())
    built = json.loads((batch / 'build_plan.json').read_text())
    forced = set()
    if args.flipbooks:
        for entry in original:
            data = json.loads((Path(entry['conversion']) / 'armor.json').read_text())
            if any((layer.get('stepped') or layer.get('stepped_rect')) and layer['period'] > 1
                   for gender in data['genders'].values() for renderer in gender['renderers'].values()
                   for layer in renderer.get('layers') or []):
                forced.add(entry['native_bundle_id'])
    built = [entry for entry in built if entry['native_bundle_id'] not in forced]
    accepted = {entry['native_bundle_id'] for entry in built}
    retries = [entry for entry in original if entry['native_bundle_id'] not in accepted]
    assert retries
    held = json.loads((batch / 'held_candidates.json').read_text())
    native.write(batch / 'recovery_before.json', dict(held=held, accepted=sorted(accepted)))
    retry_ids = {entry['native_bundle_id'] for entry in retries}
    held = [entry for entry in held if entry['bundle_id'] not in retry_ids]
    build_armor.sync_project(str(native.PROJECT))
    for entry in retries:
        data = json.loads((Path(entry['conversion']) / 'armor.json').read_text())
        periods = {layer['period'] for gender in data['genders'].values()
                   for renderer in gender['renderers'].values() for layer in renderer.get('layers') or []}
        entry['frames'] = sorted(set(entry['frames']) | {frame for period in periods if period > 1
            for frame in (5 * period - 1, 5 * period, 5 * period + 1)})
        input_data = json.loads(Path(entry['unity_input']).read_text())
        input_data['validateFrames'] = entry['frames']
        input_path = batch / entry['key'] / 'recovery_input.json'
        native.write(input_path, input_data)
        entry['unity_input'] = str(input_path)

    # Force a validation exception after a clone/camera exists, then capture real
    # items in the same Unity process. No fault output enters the publication plan.
    fault = deepcopy(json.loads(Path(retries[0]['unity_input']).read_text()))
    fault_folder = batch / 'expected_capture_failure'
    fault_folder.mkdir(exist_ok=True)
    (fault_folder / 'build_failure.txt').unlink(missing_ok=True)
    fault.update(outputDir=str(fault_folder), bundleName='expected_capture_failure.unity3d', validateFrames=[-1])
    fault_input = fault_folder / 'input.json'
    native.write(fault_input, fault)
    for entry in retries:
        (Path(entry['bundle']).parent / 'build_failure.txt').unlink(missing_ok=True)
    job = batch / 'unity_recovery.json'
    native.write(job, dict(inputs=[str(fault_input)] + [entry['unity_input'] for entry in retries]))
    startup = subprocess.STARTUPINFO()
    startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startup.wShowWindow = subprocess.SW_HIDE
    result = subprocess.run([build_armor.UNITY, '-batchmode', '-quit', '-projectPath', str(native.PROJECT),
        '-executeMethod', 'ArmorBuilder.BuildMany', '-armorBatchInput', str(job),
        '-logFile', str(batch / 'unity_recovery.log')], startupinfo=startup,
        creationflags=subprocess.CREATE_NO_WINDOW, timeout=3600)
    assert (fault_folder / 'build_failure.txt').exists(), 'Expected isolation failure was not exercised'
    native.write(batch / 'capture_isolation_test.json', dict(status='failure_exercised',
        expected_failure=(fault_folder / 'build_failure.txt').read_text(), unity_exit=result.returncode,
        subsequent_bundle_ids=[entry['native_bundle_id'] for entry in retries]))
    native.finalize(batch, retries, held, len(original), built=built, prepared_count=len(original))
    final = json.loads((batch / 'build_plan.json').read_text())
    passed = retry_ids <= {entry['native_bundle_id'] for entry in final}
    report = json.loads((batch / 'capture_isolation_test.json').read_text())
    report.update(status='passed' if passed else 'partial', recovered=len(final) - len(built))
    native.write(batch / 'capture_isolation_test.json', report)
    print('RECOVERY', report['status'], report['recovered'], '/', len(retries), flush=True)


if __name__ == '__main__':
    main()
