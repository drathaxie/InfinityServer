"""Read-only preflight; run on the configured InfinityServer host environment."""
import hashlib
import argparse
import json
from pathlib import Path
import db

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--native-bundle-id', type=int, default=30402)
parser.add_argument('--source-filename', default='items/swords/OmegaRapier.swf')
parser.add_argument('--prefab-name', default='OmegaRapier_FlashTrial')
parser.add_argument('--trial-name', default='Omega Rapier Flash Trial')
parser.add_argument('--bundle-stem', default='omega_rapier_flash_trial')
args = parser.parse_args()
source_relative = Path(args.source_filename)
assert source_relative.parent == Path('items/swords') and source_relative.suffix == '.swf'
assert db.BACKEND == 'mysql'
c = db.connect()
catalog_path = Path('/opt/infinity/data/asset_bundles.json')
catalog = json.loads(catalog_path.read_text())
players = [dict(r) for r in c.execute(
    'SELECT id,account_id,name,level FROM characters WHERE LOWER(name)=LOWER(?)', ('redux',)).fetchall()]
max_id = c.execute('SELECT MAX(id) AS maximum FROM items').fetchone()['maximum']
# Trial items live in 2000000-2099999 (deploy_live.py asserts it). Other reserved ranges sit above it,
# e.g. SwordShine's wiki archive currencies at 3000000+, so take the next id inside the trial range.
trial_max = c.execute('SELECT MAX(id) AS maximum FROM items WHERE id BETWEEN 2000000 AND 2099999').fetchone()['maximum']
candidate_item = max(int(trial_max or 1999999) + 1, 2000000)
while c.execute('SELECT id FROM items WHERE id=?', (candidate_item,)).fetchone():
    candidate_item += 1
candidate_bundle = max(max(int(k) for k in catalog) + 1, 2000000)
while str(candidate_bundle) in catalog or c.execute('SELECT bundle_id FROM inf_asset_bundles WHERE bundle_id=?', (candidate_bundle,)).fetchone():
    candidate_bundle += 1
examples = [dict(r) for r in c.execute(
    "SELECT id,Name,prefab_name,bundle,item_type,equip_spot,Level,DPS,Quantity,Stack,File,Type,Equipment FROM items "
    "WHERE item_type=6 AND equip_spot=2 AND bundle IS NOT NULL ORDER BY id LIMIT 3").fetchall()]
out = {'backend': db.BACKEND, 'players': players, 'maximum_item_id': max_id,
       'suggested_unused_item_id': candidate_item, 'suggested_unused_bundle_id': candidate_bundle,
       'bundle_catalog_count': len(catalog), 'bundle_catalog_sha256': hashlib.sha256(catalog_path.read_bytes()).hexdigest(),
       'native_bundle_id': args.native_bundle_id,
       'existing_native_bundle': catalog.get(str(args.native_bundle_id)), 'sword_examples': examples,
       'existing_trial_items': [dict(r) for r in c.execute(
           "SELECT id,Name,prefab_name,bundle FROM items WHERE prefab_name=? OR Name=?",
           (args.prefab_name, args.trial_name)).fetchall()],
       'existing_trial_bundles': {k:v for k,v in catalog.items() if args.bundle_stem in str(v).lower()}}
out['default_sword_template'] = db.item(c, 1)
items_path = Path('/opt/infinity/data/items.json')
seed_items = json.loads(items_path.read_text())
out['seed_items_type'] = type(seed_items).__name__
out['seed_items_sha256'] = hashlib.sha256(items_path.read_bytes()).hexdigest()
out['seed_candidate_absent'] = str(candidate_item) not in seed_items if isinstance(seed_items, dict) else all(int(r.get('ID',0)) != candidate_item for r in seed_items)
if c.execute("SELECT table_name FROM information_schema.tables WHERE table_schema=DATABASE() AND table_name='inf_asset_bundles'").fetchone():
    columns = [r[0] for r in c.execute("SELECT column_name FROM information_schema.columns WHERE table_schema=DATABASE() AND table_name='inf_asset_bundles'").fetchall()]
    key = next(name for name in columns if name.lower() in ('id','bundleid','bundle_id'))
    out['shared_inf_asset_bundle_key'] = key
    out['shared_inf_asset_bundle_candidate_rows'] = [dict(r) for r in c.execute(f'SELECT * FROM inf_asset_bundles WHERE `{key}`=?', (candidate_bundle,)).fetchall()]
classic = Path('/var/www/swordshine/gamefiles') / source_relative
out['legacy_swf'] = {'exists': classic.exists(), 'sha256': hashlib.sha256(classic.read_bytes()).hexdigest() if classic.exists() else None}
if len(players) == 1:
    player_id = players[0]['id']
    out['inventory_rows'] = c.execute('SELECT COUNT(*) AS n FROM char_items WHERE char_id=? AND banked=0', (player_id,)).fetchone()['n']
    out['equipped_weapon'] = [dict(r) for r in c.execute(
        'SELECT ci.char_item_id,ci.item_id,i.Name,ci.quantity,ci.equipped,ci.banked FROM char_items ci '
        'JOIN items i ON i.id=ci.item_id WHERE ci.char_id=? AND ci.equipped=1 AND i.equip_spot=2', (player_id,)).fetchall()]
c.close()
print(json.dumps(out, indent=2, default=str))
