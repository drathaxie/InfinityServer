"""Create a guarded redux sword-trial payload from a validated build and live preflight.

This writes a local reviewable payload only. deploy_live.py checks drift again.
"""
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--trial', type=Path, required=True)
    parser.add_argument('--name', required=True)
    parser.add_argument('--source-filename', required=True)
    parser.add_argument('--linkage', required=True)
    args = parser.parse_args()
    conversion = args.trial / 'conversion'
    preflight = json.loads((conversion / 'live_preflight.json').read_text(encoding='utf-8-sig'))
    report = json.loads((conversion / 'render_validation.json').read_text())
    config = json.loads((conversion / 'UnityProject/trial_config.json').read_text())
    assert report['status'] == 'passed' and report['unity_checked'] and not report['failures']
    assert preflight['backend'] == 'mysql' and not preflight['existing_trial_items']
    assert not preflight['existing_trial_bundles'] and preflight['seed_candidate_absent']
    assert not preflight['shared_inf_asset_bundle_candidate_rows']
    assert [(r['id'], r['account_id'], r['name']) for r in preflight['players']] == [(8, 8, 'redux')]
    source = args.trial / 'source' / Path(args.source_filename).name
    assert sha(source) == preflight['legacy_swf']['sha256']
    binary = conversion / 'bundle' / config['bundleName']
    assert sha(binary) == report['bundle']['sha256'] and binary.read_bytes().startswith(b'UnityFS')
    item_id, bundle_id = preflight['suggested_unused_item_id'], preflight['suggested_unused_bundle_id']
    bundle_filename = 'items/swords/' + config['bundleName']
    bundle = {'ID': bundle_id, 'Name': args.name, 'Type': 'ITEM', 'FileName': bundle_filename,
              'Version': 1, 'VersionContent': 1, 'VersionStage': 1, 'VersionLive': 1,
              'DependencyID': 0, 'CRCPC': str(report['bundle']['windows_crc']), '_cdnvL': 1}
    inline_bundle = {key: value for key, value in bundle.items() if key not in ('FileName', '_cdnvL')}
    inline_bundle['Filename'] = bundle_filename
    item = deepcopy(preflight['default_sword_template'])
    item.update(ID=item_id, Name=args.name, Description='An animated Caladbolg conversion trial.' if args.linkage == 'Caladbolg'
                else 'An animated Flash-to-Unity conversion trial.', Linkage=args.linkage,
                Filename=args.source_filename, PrefabName=config['prefabName'], Bundle=inline_bundle,
                MobileCompatibility=1, isClass=False, House=False, HouseInventory=False)
    payload = {'item_id': item_id, 'bundle_id': bundle_id, 'char_id': 8, 'account_id': 8,
               'char_name': 'redux', 'item': item, 'bundle': bundle,
               'bundle_sha256': report['bundle']['sha256'],
               'bundle_target': '/opt/infinity/content/game/assetbundles/windows/items/swords/'
                   + Path(config['bundleName']).stem + '/1/' + config['bundleName'],
               'catalog_sha256': preflight['bundle_catalog_sha256'], 'seed_sha256': preflight['seed_items_sha256'],
               'native_bundle_id': preflight['native_bundle_id'], 'native_bundle': preflight['existing_native_bundle'],
               'source_swf_sha256': sha(source),
               'reason': 'User requested the animated ' + args.linkage + ' Flash-to-Unity trial in redux inventory.'}
    target = conversion / 'deployment_payload.json'
    target.write_text(json.dumps(payload, indent=2), encoding='utf-8')
    print(json.dumps({'status': 'prepared_only', 'payload': str(target), 'item_id': item_id,
                      'bundle_id': bundle_id, 'bundle_sha256': payload['bundle_sha256']}, indent=2))


if __name__ == '__main__':
    main()
