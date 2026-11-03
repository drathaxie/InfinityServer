"""Read-only live preflight for the separate Flying Moglin trial; run with server env."""
import hashlib
import json
from pathlib import Path
import subprocess
import db


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


c = db.connect()
try:
    assert db.BACKEND == 'mysql'
    registry_path = Path('/opt/infinity/data/asset_bundles.json')
    seed_path = Path('/opt/infinity/data/items.json')
    registry = json.loads(registry_path.read_text())
    seeds = json.loads(seed_path.read_text())
    character = dict(c.execute('SELECT id,account_id,name FROM characters WHERE id=?', (8,)).fetchone())
    assert character == dict(id=8, account_id=8, name='redux')
    original = dict(c.execute('SELECT * FROM items WHERE id=?', (323,)).fetchone())
    assert original['Name'] == 'Flying Moglin' and original['File'] == 'items/pets/moglinwings.swf'
    assert original['bundle'] is None
    source = Path('/var/www/swordshine/gamefiles/items/pets/moglinwings.swf')
    iid = bid = 2000000
    while str(iid) in seeds or c.execute('SELECT id FROM items WHERE id=?', (iid,)).fetchone():
        iid += 1
    while str(bid) in registry or c.execute('SELECT bundle_id FROM inf_asset_bundles WHERE bundle_id=?', (bid,)).fetchone():
        bid += 1
    existing = [dict(r) for r in c.execute('SELECT id,Name FROM items WHERE prefab_name=?', ('FlyingMoglin_FlashTrial',)).fetchall()]
    assert not existing
    loader = Path('/opt/infinity/data/mod/InfinityLoader.dll')
    print(json.dumps(dict(backend=db.BACKEND, character=character, original_pet=original,
        suggested_unused_item_id=iid, suggested_unused_bundle_id=bid,
        catalog_sha256=sha(registry_path), seed_sha256=sha(seed_path),
        native_bundle_id=49060, native_bundle=registry['49060'],
        source_swf_sha256=sha(source), loader_sha256=sha(loader),
        services=subprocess.check_output(['systemctl', 'is-active', 'infinity-api', 'infinity-game'], text=True).splitlines()),
        indent=2, default=str))
finally:
    c.close()
