"""Package a validated armor trial and local rebuild inputs, excluding caches and live account snapshots."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import zipfile


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--trial', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    trial = args.trial.resolve()
    bundle_dir = trial / 'conversion/bundle'
    report = json.loads((bundle_dir / 'build_validation.json').read_text())
    audit = json.loads((bundle_dir / 'bundle_audit.json').read_text())
    assert report['status'] == audit['status'] == 'passed'
    bundles = list(bundle_dir.glob('*.unity3d'))
    assert len(bundles) == 1
    binary = bundles[0]
    assert sha(binary) == audit['sha256']
    tools_dir = trial / 'tools/swf2unity'
    tools_dir.mkdir(parents=True, exist_ok=True)
    for name in ('armor.py', 'armor_template.py', 'build_armor.py', 'validate_armor.py',
                 'ccscripts.py', 'render.py', 'rig.py', 'swfparse.py', 'inspect_bundle.py'):
        shutil.copyfile(Path(__file__).with_name(name), tools_dir / name)
    source_assets = Path(__file__).parent / 'unity/Assets'
    for source in source_assets.rglob('*'):
        if source.is_file() and source.suffix in ('.cs', '.shader'):
            target = tools_dir / 'unity/Assets' / source.relative_to(source_assets)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
    files = []
    for folder in ('source', 'conversion/armor', 'conversion/template', 'evidence',
                   'tools/swf2unity', 'conversion/bundle/unity_validation'):
        for path in (trial / folder).rglob('*'):
            relative = path.relative_to(trial)
            if path.is_file() and not set(relative.parts).intersection({'work', '__pycache__', 'Library', 'Temp', 'Logs'}) and path.suffix not in ('.log', '.pyc'):
                files.append(path)
    for name in ('README.md', 'rebuild.ps1', 'trial_manifest.json', 'import_plan.json',
                 'conversion/bundle/build_validation.json', 'conversion/bundle/bundle_audit.json',
                 'conversion/bundle/unity_report.txt', 'conversion/bundle/unity_vs_emulation.json',
                 'conversion/UnityProject/Packages/manifest.json',
                 'conversion/UnityProject/ProjectSettings/ProjectVersion.txt'):
        path = trial / name
        if path.is_file():
            files.append(path)
    files.extend((binary, binary.with_suffix(binary.suffix + '.manifest')))
    files = sorted(set(files))
    entries = [{'path': path.relative_to(trial).as_posix(), 'bytes': path.stat().st_size,
                'sha256': sha(path)} for path in files]
    integrity = trial / 'package_integrity.json'
    integrity.write_text(json.dumps({'scope': 'All ZIP members except this integrity file', 'files': entries}, indent=2))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(args.output, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for path in [*files, integrity]:
            archive.write(path, path.relative_to(trial).as_posix())
    with zipfile.ZipFile(args.output) as archive:
        assert archive.testzip() is None
        for entry in entries:
            assert hashlib.sha256(archive.read(entry['path'])).hexdigest() == entry['sha256']
    result = {'file': str(args.output.resolve()), 'bytes': args.output.stat().st_size,
              'sha256': sha(args.output), 'members_verified': len(entries) + 1}
    args.output.with_suffix(args.output.suffix + '.sha256').write_text(result['sha256'] + '  ' + args.output.name + '\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
