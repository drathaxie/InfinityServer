"""Install the verified preview loader atomically; a running client keeps its loaded build."""
from datetime import datetime, timezone
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil

root = Path(__file__).resolve().parents[2]
trial = root / 'outputs/flash_converter_trial/FlyingMoglin'
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--payload', type=Path, default=trial / 'loader_payload.json')
parser.add_argument('--result', type=Path, default=trial / 'local_loader_installation.json')
args = parser.parse_args()
payload = json.loads(args.payload.read_text())
source = root / 'data/mod/InfinityLoader.dll'
directory = Path(r'C:\Program Files (x86)\Steam\steamapps\common\AdventureQuest Worlds Unity Playtest')
target = directory / 'InfinityLoader.dll'
staged = directory / 'InfinityLoader.dll.preview-new'
assert not staged.exists(), 'An earlier loader staging file needs review'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


assert sha(source) == payload['new_sha256']
before = sha(target)
assert before in (payload['expected_before_sha256'], payload['new_sha256'])
if before == payload['new_sha256']:
    result = dict(status='already_installed', sha256=before, target=str(target))
else:
    backup = directory / datetime.now(timezone.utc).strftime('InfinityServer-backup-preview-%Y%m%dT%H%M%SZ')
    backup.mkdir()
    shutil.copy2(target, backup / target.name)
    assert sha(backup / target.name) == before
    shutil.copy2(source, staged)
    assert sha(staged) == payload['new_sha256']
    assert sha(target) == before
    os.replace(staged, target)
    assert sha(target) == payload['new_sha256']
    result = dict(status='installed_for_next_start', sha256=sha(target), target=str(target), backup=str(backup))
args.result.write_text(json.dumps(result, indent=2))
print(json.dumps(result))
