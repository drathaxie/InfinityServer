"""Package a validated animated sword trial without Unity caches or account logs."""
from __future__ import annotations

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
    parser.add_argument("--trial", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    trial = args.trial.resolve()
    report = json.loads((trial / "conversion/render_validation.json").read_text(encoding="utf-8"))
    if report["status"] != "passed" or not report["unity_checked"]:
        raise ValueError("Unity render validation must pass before packaging")
    config = json.loads((trial / "conversion/UnityProject/trial_config.json").read_text(encoding="utf-8"))
    bundle_name = config.get('bundleName', 'omega_rapier_flash_trial.unity3d')
    bundle = trial / "conversion/bundle" / bundle_name
    if sha(bundle) != report["bundle"]["sha256"]:
        raise ValueError("bundle changed after validation")
    tools_dir = trial / "tools/flash_trial"
    tools_dir.mkdir(parents=True, exist_ok=True)
    for name in ("build_trial.ps1", "prepare_project.py", "validate_trial.py", "FlashTrialBuilder.cs"):
        shutil.copyfile(Path(__file__).with_name(name), tools_dir / name)
    files = []
    for folder in ("source", "reference", "evidence", "tools", "conversion/source_capture/canonical",
                   "conversion/UnityProject/Assets", "conversion/UnityProject/Packages", "conversion/bundle/unity_frames",
                   "conversion/runtime_contract"):
        files.extend(path for path in (trial / folder).rglob("*") if path.is_file())
    # Preserve the source capture recipe and timing proof without its export scratch directories.
    for pattern in ('*.py', '*.json', '*.md'):
        files.extend((trial / 'conversion/source_capture').glob(pattern))
    if config['frameCount'] > 256:
        # Keep long-loop evidence compact. A rebuild regenerates every numbered GPU frame.
        frame_dir = trial / 'conversion/bundle/unity_frames'
        representatives, schedule = {}, []
        for index in range(config['frameCount']):
            frame_path = frame_dir / f'frame_{index+1:02}.png'
            digest = sha(frame_path)
            representative = representatives.setdefault(digest, frame_path)
            schedule.append({'frame': index+1, 'file': representative.relative_to(trial).as_posix(),
                             'sha256': digest})
        retained = set(representatives.values())
        files = [path for path in files if path.parent != frame_dir or not path.name.startswith('frame_') or path in retained]
        reuse_path = trial / 'conversion/bundle/gpu_frame_reuse.json'
        reuse_path.write_text(json.dumps({'frames': config['frameCount'], 'unique_pngs': len(retained),
            'note': 'Identical GPU PNGs are packaged once; rebuild.ps1 regenerates all numbered captures.',
            'schedule': schedule}, indent=2), encoding='utf-8')
        files.append(reuse_path)
    for name in ("README.md", "rebuild.ps1", "trial_manifest.json", "import_plan.json",
                 "conversion/source_capture/capture_manifest.json", "conversion/render_validation.json",
                 "conversion/prepared_validation.json", "conversion/gpu_first_frame_comparison.png",
                 "conversion/unity_playback.webp", "conversion/UnityProject/trial_config.json",
                 "conversion/UnityProject/ProjectSettings/ProjectVersion.txt",
                 "conversion/bundle/" + bundle_name,
                 "conversion/bundle/" + bundle_name + ".manifest",
                 "conversion/bundle/unity_validation.json", "conversion/bundle/preparation.json"):
        path = trial / name
        if path.is_file():
            files.append(path)
    files = sorted(set(files))
    entries = [{"path": p.relative_to(trial).as_posix(), "bytes": p.stat().st_size, "sha256": sha(p)} for p in files]
    integrity = trial / "package_integrity.json"
    integrity.write_text(json.dumps({"scope": "All ZIP members except this self-describing integrity file",
                                     "files": entries}, indent=2), encoding="utf-8")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(args.output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in [*files, integrity]:
            archive.write(path, path.relative_to(trial).as_posix())
    with zipfile.ZipFile(args.output) as archive:
        if archive.testzip() is not None:
            raise ValueError("ZIP integrity failure")
        for entry in entries:
            if hashlib.sha256(archive.read(entry["path"])).hexdigest() != entry["sha256"]:
                raise ValueError(f"ZIP member differs: {entry['path']}")
    result = {"file": str(args.output.resolve()), "bytes": args.output.stat().st_size,
              "sha256": sha(args.output), "members_verified": len(entries) + 1}
    args.output.with_suffix(args.output.suffix + ".sha256").write_text(
        result["sha256"] + "  " + args.output.name + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
