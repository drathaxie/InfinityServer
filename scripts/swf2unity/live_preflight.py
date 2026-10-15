"""Read-only preflight for the Cryo Chronomancer armor trial on the active live backend."""
import hashlib
import json
from pathlib import Path
import subprocess
import db


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


c = db.connect()
try:
    assert db.BACKEND == 'mysql'
    registry_path = Path('/opt/infinity/data/asset_bundles.json')
    seed_path = Path('/opt/infinity/data/items.json')
    registry, seeds = json.loads(registry_path.read_text()), json.loads(seed_path.read_text())
    original = db.item(c, 82191)
    assert original['Name'] == 'Cryo Chronomancer' and original['Bundle']['ID'] == 49060
    assert (original['ItemType'], original['EquipSpot']) == (22, 7)
    player = dict(c.execute('SELECT id,account_id,name FROM characters WHERE id=?', (8,)).fetchone())
    assert player == {'id': 8, 'account_id': 8, 'name': 'redux'}
    item_id = 2000000  # independently probe the dedicated local trial namespace
    while str(item_id) in seeds or c.execute('SELECT id FROM items WHERE id=?', (item_id,)).fetchone():
        item_id += 1
    bundle_id = 2000000
    while str(bundle_id) in registry or c.execute('SELECT bundle_id FROM inf_asset_bundles WHERE bundle_id=?', (bundle_id,)).fetchone():
        bundle_id += 1
    sources = {}
    for gender in ('M', 'F'):
        relative = 'classes/' + gender + '/ChronoFrostMage.swf'
        path = Path('/var/www/swordshine/gamefiles') / relative
        sources[gender] = {'relative': relative, 'sha256': sha(path), 'bytes': path.stat().st_size}
    existing = [dict(r) for r in c.execute(
        "SELECT id,Name,prefab_name,bundle FROM items WHERE Name=? OR bundle LIKE ?",
        ('Cryo Chronomancer Flash Trial', '%chronofrostmage_flash%')).fetchall()]
    result = {'backend': db.BACKEND, 'character': player, 'original_item': original,
              'native_bundle_id': 49060, 'native_bundle': registry['49060'],
              'suggested_unused_item_id': item_id, 'suggested_unused_bundle_id': bundle_id,
              'catalog_sha256': sha(registry_path), 'seed_sha256': sha(seed_path),
              'source_files': sources, 'existing_trials': existing,
              'existing_trial_bundles': {k:v for k,v in registry.items() if 'chronofrostmage_flash' in str(v).lower()},
              'equipped': [dict(r) for r in c.execute('SELECT * FROM char_items WHERE char_id=? AND equipped=1 ORDER BY char_item_id', (8,)).fetchall()],
              'services': subprocess.check_output(['systemctl', 'is-active', 'infinity-api', 'infinity-game'], text=True).splitlines()}
    print(json.dumps(result, indent=2, default=str))
finally:
    c.close()
