"""Verify public pet delivery, redux's next-login wire, and preserved original data."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import urllib.request
import db
import game


def read(url):
    with urllib.request.urlopen(url, timeout=30) as response:
        assert response.status == 200
        return response.read()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--payload', type=Path, required=True)
    args = parser.parse_args()
    p = json.loads(args.payload.read_text())
    content = 'https://content.130-162-189-229.sslip.io/' + Path(p['bundle_target']).relative_to('/opt/infinity/content').as_posix()
    binary = read(content)
    assert hashlib.sha256(binary).hexdigest() == p['bundle_sha256']
    api = json.loads(read('https://divinityarts.mooo.com/data/GetAssetBundlesByIDs?ids=' + str(p['bundle_id'])))
    bundle = next(b for b in api if b['ID'] == p['bundle_id'])
    assert (bundle.get('Filename') or bundle.get('FileName')) == p['bundle']['FileName']
    assert bundle['CRCPC'] == p['bundle']['CRCPC'] and bundle['Type'] == 'NPC'
    c = db.connect()
    try:
        rows = [dict(r) for r in c.execute('SELECT * FROM char_items WHERE char_id=? AND item_id=?', (8, p['item_id'])).fetchall()]
        assert len(rows) == 1 and rows[0]['quantity'] == 1 and not rows[0]['banked']
        wire = next(i for i in game.inventory(c, 8) if i['ID'] == p['item_id'])
        assert (wire['ItemType'], wire['EquipSpot']) == (18, 5)
        assert wire['PrefabName'] == 'FlyingMoglin_FlashTrial' and wire['MobileCompatibility'] == 1
        assert wire['Bundle']['ID'] == p['bundle_id']
        original = dict(c.execute('SELECT * FROM items WHERE id=?', (323,)).fetchone())
        assert json.loads(json.dumps(original, default=str)) == p['original_pet']
        shared = dict(c.execute('SELECT * FROM inf_asset_bundles WHERE bundle_id=?', (p['bundle_id'],)).fetchone())
        assert shared['filename'] == p['bundle']['FileName'] and shared['version_live'] == 1
    finally:
        c.close()
    loader_payload = json.loads(args.payload.with_name('loader_payload.json').read_text())
    loader_sha = read('https://divinityarts.mooo.com/mod/InfinityLoader.dll.sha256').decode().strip()
    loader_binary = read('https://divinityarts.mooo.com/mod/InfinityLoader.dll')
    assert loader_sha == hashlib.sha256(loader_binary).hexdigest() == loader_payload['new_sha256']
    services = subprocess.check_output(['systemctl', 'is-active', 'infinity-api', 'infinity-game'], text=True).splitlines()
    assert services == ['active', 'active']
    print(json.dumps(dict(status='passed', item_id=p['item_id'], public_bytes=len(binary),
        bundle_sha256=p['bundle_sha256'], public_bundle=bundle, inventory_wire=wire,
        original_pet_preserved=True, loader_sha256=loader_sha, services=services,
        proof_boundary='Public delivery and next-login inventory verified; equip, following, size, and rendered armor previews require client observation.'), default=str))


if __name__ == '__main__':
    main()
