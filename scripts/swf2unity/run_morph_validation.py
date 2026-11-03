"""Run the exact loader component in a separate Unity validation project."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

from build_armor import UNITY
from trial_host import ROOT

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('bundle', type=Path)
parser.add_argument('project', type=Path)
parser.add_argument('result', type=Path)
parser.add_argument('--validator', choices=('MorphValidation', 'RandomVariantValidation'), default='MorphValidation')
args = parser.parse_args()
project, result = args.project.resolve(), args.result.resolve()
runtime = project / 'Assets/FlashMorphValidation'
editor = project / 'Assets/Editor'
runtime.mkdir(parents=True, exist_ok=True)
editor.mkdir(parents=True, exist_ok=True)
old = editor / 'FlashRigMorph.cs'
for path in [old, old.with_suffix('.cs.meta')]:
    if path.exists():
        assert path.is_relative_to(project / 'Assets/Editor') and path.is_file()
        path.unlink()
source = ROOT / 'mod/InfinityLoader/FlashRigMorph.cs'
shutil.copyfile(source, runtime / source.name)
managed_json = Path(os.environ.get('AQWI_GAME_DIR', r'C:/Program Files (x86)/Steam/steamapps/common/AdventureQuest Worlds Unity Playtest')) / 'AdventureQuest Worlds Infinity_Data/Managed/Newtonsoft.Json.dll'
shutil.copyfile(managed_json, runtime / 'Newtonsoft.Json.dll')
shutil.copyfile(ROOT / 'outputs/flash_converter_trial/interactive_morph/validation_sources/MorphPointerProbes.cs',
                runtime / 'MorphPointerProbes.cs')
(runtime / 'AssemblyInfo.cs').write_text('using System.Runtime.CompilerServices;\n[assembly: InternalsVisibleTo("Assembly-CSharp-Editor")]\n')
shutil.copyfile(ROOT / ('outputs/flash_converter_trial/interactive_morph/validation_sources/' + args.validator + '.cs'),
                editor / (args.validator + '.cs'))
input_path = result.with_suffix('.input.json')
input_path.write_text(json.dumps(dict(bundle=str(args.bundle.resolve()), output=str(result))))
log = result.with_suffix('.log')
startup = subprocess.STARTUPINFO()
startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
startup.wShowWindow = subprocess.SW_HIDE
process = subprocess.run([UNITY, '-batchmode', '-quit', '-projectPath', str(project), '-executeMethod',
    args.validator + '.Run', '-morphInput', str(input_path), '-logFile', str(log)], startupinfo=startup,
    creationflags=subprocess.CREATE_NO_WINDOW, timeout=1800)
text = log.read_text(errors='replace')
assert process.returncode == 0 and 'MORPH_VALIDATION_SUCCESS' in text, text[-6000:]
proof = json.loads(result.read_text())
proof['runtime_source_sha256'] = hashlib.sha256(source.read_bytes()).hexdigest()
proof['bundle_sha256'] = hashlib.sha256(args.bundle.read_bytes()).hexdigest()
result.write_text(json.dumps(proof, indent=2))
print(json.dumps(proof), flush=True)
