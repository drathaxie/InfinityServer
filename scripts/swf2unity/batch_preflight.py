"""Read-only source, identity and free-ID preflight for batch02; run on the live host."""
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
    char = dict(c.execute('SELECT id,account_id,name FROM characters WHERE id=?', (8,)).fetchone())
    assert char == dict(id=8, account_id=8, name='redux')
    originals, templates, hashes = {}, {}, {}
    sources = globals().get('SOURCES', [dict(id=44489, relatives=['classes/M/ChronoStriker.swf', 'classes/F/ChronoStriker.swf']),
                                       dict(id=74419, relatives=['items/swords/DarkMatterDragonBlade.swf'])])
    for source in sources:
        source_id, relatives = source['id'], source['relatives']
        original = dict(c.execute('SELECT * FROM items WHERE id=?', (source_id,)).fetchone())
        if source.get('native_bundle_id') is not None:
            assert json.loads(original['bundle'])['ID'] == source['native_bundle_id']
        else:
            assert original['bundle'] is None
        originals[str(source_id)] = original
        templates[str(source_id)] = db.item(c, source_id)
        hashes.update({relative: sha(Path('/var/www/swordshine/gamefiles') / relative) for relative in relatives})
        if source.get('name'):
            assert not c.execute('SELECT id FROM items WHERE Name=?', (source['name'],)).fetchone()
    item_ids, bundle_ids = [], []
    for candidate in range(2000000, 2100000):
        if str(candidate) not in seeds and not c.execute('SELECT id FROM items WHERE id=?', (candidate,)).fetchone():
            item_ids.append(candidate)
        if str(candidate) not in registry and not c.execute('SELECT bundle_id FROM inf_asset_bundles WHERE bundle_id=?', (candidate,)).fetchone():
            bundle_ids.append(candidate)
        if len(item_ids) >= len(sources) and len(bundle_ids) >= len(sources):
            break
    assert len(item_ids) >= len(sources) and len(bundle_ids) >= len(sources)
    print(json.dumps(dict(backend=db.BACKEND, character=char, originals=originals, templates=templates,
        source_hashes=hashes, item_ids=item_ids[:len(sources)], bundle_ids=bundle_ids[:len(sources)],
        catalog_sha256=sha(registry_path), seed_sha256=sha(seed_path),
        services=subprocess.check_output(['systemctl', 'is-active', 'infinity-api', 'infinity-game'], text=True).splitlines()), default=str))
finally:
    c.close()
