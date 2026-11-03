"""Stage, publish and verify the bounded Flying Moglin + armor-preview trial continuation."""
import argparse
import hashlib
import io
import json
from pathlib import Path
import shlex
import subprocess
import tarfile

ROOT = Path(__file__).resolve().parents[2]
TRIAL = ROOT / 'outputs/flash_converter_trial/FlyingMoglin'
SCRIPTS = ROOT / 'scripts/swf2unity'
REMOTE = '/tmp/infinity-flying-moglin-20261012'
SSH = ['ssh', '-i', str(Path.home() / 'Downloads/ssh-key-2026-06-19.key'),
       '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=15', 'ubuntu@130.162.189.229']
ENV = 'cd /opt/infinity/server && set -a && . ./.my.env && set +a && PYTHONPATH=/opt/infinity/server '


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def remote(command, data=None, timeout=90):
    result = subprocess.run(SSH + [command], input=data, capture_output=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError(result.stderr.decode(errors='replace') + result.stdout.decode(errors='replace'))
    return result.stdout


def server_script(script, arguments='', data=None):
    command = ENV + '/opt/infinity/.venv/bin/python ' + script + (' ' + arguments if arguments else '')
    return remote('sudo -n bash -c ' + shlex.quote(command), data)


def preflight():
    pf = json.loads(server_script('-', data=(SCRIPTS / 'pet_preflight.py').read_bytes()))
    (TRIAL / 'live_preflight.json').write_text(json.dumps(pf, indent=2))
    source = ROOT / 'outputs/flash_converter_trial/pet_candidates/moglinwings.swf'
    assert sha(source) == pf['source_swf_sha256']
    binary = TRIAL / 'bundle/flying_moglin_flash_trial.unity3d'
    audit = json.loads((TRIAL / 'bundle/bundle_contract.json').read_text())
    gpu = json.loads((TRIAL / 'bundle/unity_validation_summary.json').read_text())
    movement = json.loads((TRIAL / 'bundle/pet_movement_validation.json').read_text())
    assert audit['status'] == gpu['status'] == movement['status'] == 'passed'
    iid, bid = pf['suggested_unused_item_id'], pf['suggested_unused_bundle_id']
    filename = 'npcs/' + binary.name
    name = 'Flying Moglin Flash Trial'
    bundle = dict(ID=bid, Name=name, Type='NPC', FileName=filename, Version=1,
        VersionContent=1, VersionStage=1, VersionLive=1, DependencyID=0,
        CRCPC=str(audit['bundleCRC']), _cdnvL=1)
    inline = {k: v for k, v in bundle.items() if k not in ('FileName', '_cdnvL')}
    inline['Filename'] = filename
    item = dict(ID=iid, Name=name, Description='Animated Flying Moglin Flash-to-Unity conversion trial.',
        ItemType=18, EquipSpot=5, Linkage='PetMoglinwings', Icon='iipet', Level=1,
        Quantity=1, StackSize=1, Rarity=10, Filename='items/pets/moglinwings.swf',
        Coins=False, Cost=0, UpgradeOnly=False, isClass=False, House=False,
        HouseInventory=False, PrefabName='FlyingMoglin_FlashTrial', MobileCompatibility=1,
        ReqQuests=[], boostValues={}, Bundle=inline)
    payload = dict(item_id=iid, bundle_id=bid, char_id=8, account_id=8, char_name='redux',
        item=item, bundle=bundle, bundle_sha256=sha(binary),
        bundle_target='/opt/infinity/content/game/assetbundles/windows/npcs/' + binary.stem + '/1/' + binary.name,
        catalog_sha256=pf['catalog_sha256'], seed_sha256=pf['seed_sha256'],
        native_bundle_id=pf['native_bundle_id'], native_bundle=pf['native_bundle'],
        source_swf_sha256=sha(source), original_pet=pf['original_pet'],
        reason='User requested a previously unconverted pet trial; Flying Moglin granted unequipped to redux.')
    (TRIAL / 'deployment_payload.json').write_text(json.dumps(payload, indent=2))
    print(json.dumps(dict(status='prepared', item_id=iid, bundle_id=bid,
        bytes=binary.stat().st_size, loader_before=pf['loader_sha256'], services=pf['services'])))


def stage():
    files = {
        'deployment_payload.json': TRIAL / 'deployment_payload.json',
        'flying_moglin_flash_trial.unity3d': TRIAL / 'bundle/flying_moglin_flash_trial.unity3d',
        'deploy_pet_trial.py': SCRIPTS / 'deploy_pet_trial.py',
        'verify_pet_trial.py': SCRIPTS / 'verify_pet_trial.py',
        'publish_preview_loader.py': SCRIPTS / 'publish_preview_loader.py',
        'InfinityLoader.dll': ROOT / 'mod/InfinityLoader/bin/Release/InfinityLoader.dll',
        'loader_payload.json': TRIAL / 'loader_payload.json',
    }
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode='w') as archive:
        for name, path in files.items():
            archive.add(path, arcname=name)
    remote('mkdir -p ' + REMOTE + ' && tar -xf - -C ' + REMOTE, stream.getvalue())
    arguments = '--payload ' + REMOTE + '/deployment_payload.json --bundle ' + REMOTE + '/flying_moglin_flash_trial.unity3d'
    dry = json.loads(server_script(REMOTE + '/deploy_pet_trial.py', arguments))
    assert dry['status'] == 'preflight_passed'
    (TRIAL / 'deployment_dry_run.json').write_text(json.dumps(dry, indent=2))
    loader_dry = json.loads(server_script(REMOTE + '/publish_preview_loader.py',
        '--payload ' + REMOTE + '/loader_payload.json --binary ' + REMOTE + '/InfinityLoader.dll'))
    assert loader_dry['status'] == 'preflight_passed'
    (TRIAL / 'loader_dry_run.json').write_text(json.dumps(loader_dry, indent=2))
    print(json.dumps(dict(status='staged_and_dry_run_passed', item_id=dry['item_id'], bundle_id=dry['bundle_id'])))


def apply():
    dry = json.loads((TRIAL / 'deployment_dry_run.json').read_text())
    assert dry['status'] == 'preflight_passed'
    arguments = '--payload ' + REMOTE + '/deployment_payload.json --bundle ' + REMOTE + '/flying_moglin_flash_trial.unity3d --apply'
    result = json.loads(server_script(REMOTE + '/deploy_pet_trial.py', arguments))
    (TRIAL / 'deployment_result.json').write_text(json.dumps(result, indent=2))
    print(json.dumps({k: result[k] for k in ('status', 'item_id', 'bundle_id', 'backup', 'services')}))
    loader = json.loads(server_script(REMOTE + '/publish_preview_loader.py',
        '--payload ' + REMOTE + '/loader_payload.json --binary ' + REMOTE + '/InfinityLoader.dll --apply'))
    (TRIAL / 'loader_publication_result.json').write_text(json.dumps(loader, indent=2))
    print(json.dumps(loader))


def verify():
    args = '--payload ' + REMOTE + '/deployment_payload.json'
    result = json.loads(server_script(REMOTE + '/verify_pet_trial.py', args))
    (TRIAL / 'live_verification.json').write_text(json.dumps(result, indent=2))
    print(json.dumps({k: result[k] for k in ('status', 'item_id', 'public_bytes', 'services')}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('preflight', 'stage', 'apply', 'verify'))
    mode = parser.parse_args().mode
    globals()[mode]()
