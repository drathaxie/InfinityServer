"""Replace existing native bundle mappings with versioned, audited Flash conversions."""
import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import urllib.parse
import urllib.request
import db

REGISTRY = Path('/opt/infinity/data/asset_bundles.json')
SEEDS = Path('/opt/infinity/data/items.json')
CONTENT = Path('/opt/infinity/content/game/assetbundles/windows')


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def plain(value):
    return json.loads(json.dumps(value, default=str))


def services():
    result = subprocess.check_output(['systemctl', 'is-active', 'infinity-api', 'infinity-game'], text=True).splitlines()
    assert result == ['active', 'active']
    return result


def pid():
    return subprocess.check_output(['systemctl', 'show', 'infinity-game', '-p', 'MainPID', '--value'], text=True).strip()


def validate_item_aliases(bid, filename, expected_types, items):
    if all(item['Type'] in expected_types for item in items):
        return
    # Preserve single/dual weapon aliases only for identical underlying art.
    weapon_types = {'Sword','Polearm','Axe','Dagger','Mace','Staff','Bow','Gun',
                    'Gauntlet','HandGun','Rifle','Wand','Whip'}
    assert any(item['Type'] in expected_types for item in items), f'Bundle {bid} has no native category owner: {filename}'
    assert all(item['Type'] in weapon_types for item in items), f'Bundle {bid} has incompatible non-weapon aliases'
    prefabs = {(item['prefab_name'] or '').casefold() for item in items}
    assert len(prefabs)==1 and next(iter(prefabs)), f'Bundle {bid} weapon aliases differ in prefab'
    sources = {(item['File'],item['Link']) for item in items if item['File'].lower().endswith('.swf')}
    assert len(sources)==1, f'Bundle {bid} weapon aliases differ in Flash source/linkage'
    # Some original native rows refer directly to this Unity file and have no
    # Flash linkage. They are compatible only with this exact bundle filename.
    assert all(item['File'].lower().endswith('.swf') or
               (item['File'].casefold()==filename.casefold() and not item['Link']) for item in items), f'Bundle {bid} alias references different native art'


def inspect(ids, connection=None):
    assert db.BACKEND == 'mysql' and 1 <= len(ids) <= 256 and len(set(ids)) == len(ids)
    assert all(0 < i < 2000000 for i in ids)
    c = connection or db.connect()
    try:
        registry, seeds = json.loads(REGISTRY.read_text()), json.loads(SEEDS.read_text())
        marks = ','.join('?' for _ in ids)
        rows = c.execute("SELECT * FROM items WHERE JSON_VALID(bundle) AND "
            "CAST(JSON_UNQUOTE(JSON_EXTRACT(bundle,'$.ID')) AS UNSIGNED) IN (" + marks + ') ORDER BY id', tuple(ids)).fetchall()
        entries = []
        for bid in ids:
            bundle = registry[str(bid)]
            filename = bundle.get('FileName') or bundle.get('Filename')
            assert filename and '..' not in filename and '\\' not in filename and not filename.startswith('/')
            categories = {'armors/': ('Armor', 'Class'), 'items/helms/': ('Helm',), 'items/swords/': ('Sword',),
                'items/polearms/': ('Polearm',), 'items/axes/': ('Axe',), 'items/daggers/': ('Dagger',),
                'items/maces/': ('Mace',), 'items/staves/': ('Staff',), 'items/bows/': ('Bow',),
                'items/guns/': ('Gun',), 'items/gauntlets/': ('Gauntlet',),
                'items/handguns/': ('HandGun',), 'items/rifles/': ('Rifle',),
                'items/wands/': ('Wand',), 'items/whips/': ('Whip',)}
            assert filename.endswith('.unity3d') and filename.startswith(tuple(categories))
            items = [dict(r) for r in rows if int(json.loads(r['bundle'])['ID']) == bid]
            assert items and all('caladbolg' not in (r['Name'] + ' ' + r['File']).lower() for r in items)
            expected_types = next(types for prefix, types in categories.items() if filename.startswith(prefix))
            validate_item_aliases(bid,filename,expected_types,items)
            version = max(int(bundle.get(k) or 0) for k in ('Version', 'VersionContent', 'VersionStage', 'VersionLive', '_cdnvL'))
            relative = Path(filename)
            previous = CONTENT / relative.parent / relative.stem / str(version) / relative.name
            entries.append(dict(bundle_id=bid, bundle=bundle,
                bundle_row=plain(dict(row)) if (row := c.execute('SELECT * FROM inf_asset_bundles WHERE bundle_id=?', (bid,)).fetchone()) else None,
                items=plain(items), seeds={str(r['id']): seeds[str(r['id'])] for r in items if str(r['id']) in seeds},
                previous_file=dict(path=str(previous), exists=previous.is_file(),
                    sha256=sha(previous) if previous.is_file() else None)))
        return dict(entries=entries, catalog_sha256=sha(REGISTRY), seed_sha256=sha(SEEDS))
    finally:
        if connection is None:
            c.close()


def apply(payload_path, apply_changes):
    from deploy_pet_trial import publish, owned
    p = json.loads(payload_path.read_text())
    assert re.fullmatch(r'native\d{2}', p['batch']) and 1 <= len(p['entries']) <= 256
    ids = [e['bundle_id'] for e in p['entries']]
    assert plain(inspect(ids)) == p['before'], 'Native catalog or source items drifted; refresh preflight'
    old_entries = {e['bundle_id']: e for e in p['before']['entries']}
    if any(r['Type']=='Helm' for e in old_entries.values() for r in e['items']):
        assert p.get('helm_attachment_contract')==2, 'Helm publication paused pending the equipped-character visibility repair'
    for e in p['entries']:
        old = old_entries[e['bundle_id']]
        filename = old['bundle'].get('FileName') or old['bundle'].get('Filename')
        assert e['bundle'].get('FileName', e['bundle'].get('Filename')) == filename
        assert e['bundle']['ID'] == e['bundle_id']
        expected = deepcopy(old['bundle'])
        version = max([int(old['bundle'].get(k) or 0) for k in ('Version', 'VersionContent', 'VersionStage', 'VersionLive', '_cdnvL')]
            + [int(json.loads(item['bundle']).get(k) or 0) for item in old['items'] for k in ('Version', 'VersionContent', 'VersionStage', 'VersionLive')]) + 1
        expected.update({k: version for k in ('Version', 'VersionContent', 'VersionStage', 'VersionLive', '_cdnvL')})
        expected['CRCPC'] = str(e['crc'])
        assert e['bundle'] == expected and e['version'] == version
        assert all(r['prefab_name'] and r['prefab_name'].casefold() == e['prefab'].casefold() for r in old['items']), 'Shared native bundle has incompatible prefab aliases'
        relative = Path(filename)
        target = CONTENT / relative.parent / relative.stem / str(version) / relative.name
        assert str(target) == e['target'] and not target.exists()
        binary = payload_path.parent / relative.name
        assert binary.read_bytes().startswith(b'UnityFS') and sha(binary) == e['sha256']
        for source in e['sources']:
            assert '..' not in source['relative'] and not source['relative'].startswith('/')
            assert sha(Path('/var/www/swordshine/gamefiles') / source['relative']) == source['sha256']
    c = db.connect()
    stopped = committed = False
    changed = []
    game_pid = pid()
    result = dict(status='preflight_passed', bundles=ids, items=sum(len(e['items']) for e in old_entries.values()), services=services())
    try:
        if not apply_changes:
            return result
        c.execute('SELECT id FROM characters WHERE id=? FOR UPDATE', (8,)).fetchone()
        inventory = owned(c, 8)
        for old in old_entries.values():
            for r in old['items']:
                assert plain(dict(c.execute('SELECT * FROM items WHERE id=? FOR UPDATE', (r['id'],)).fetchone())) == r
        assert plain(inspect(ids, c)) == p['before']
        backup = Path('/opt/infinity/backups') / datetime.now(timezone.utc).strftime(p['batch'] + '-%Y%m%dT%H%M%SZ')
        backup.mkdir(mode=0o700)
        for path in (REGISTRY, SEEDS):
            shutil.copy2(path, backup / path.name)
        (backup / 'database-before.json').write_text(json.dumps(dict(before=p['before'], inventory=inventory), indent=2))
        shutil.copy2(payload_path, backup / 'payload.json')
        shutil.copy2(Path(__file__), backup / 'deploy_native_batch.py')
        for old in old_entries.values():
            if old['previous_file']['exists']:
                previous = Path(old['previous_file']['path'])
                assert sha(previous) == old['previous_file']['sha256']
                shutil.copy2(previous, backup / ('old-' + str(old['bundle_id']) + '.unity3d'))
        registry, seeds = json.loads(REGISTRY.read_text()), json.loads(SEEDS.read_text())
        metadata = {path: path.stat() for path in (REGISTRY, SEEDS)}
        stopped = True
        subprocess.run(['systemctl', 'stop', 'infinity-api'], check=True, timeout=30)
        assert sha(REGISTRY) == p['before']['catalog_sha256'] and sha(SEEDS) == p['before']['seed_sha256']
        touched_items = set()
        for e in p['entries']:
            old = old_entries[e['bundle_id']]
            for original in old['items']:
                inline = json.loads(original['bundle'])
                inline.update({k: e['version'] for k in ('Version', 'VersionContent', 'VersionStage', 'VersionLive')})
                inline['CRCPC'] = str(e['crc'])
                c.execute('UPDATE items SET bundle=? WHERE id=?', (json.dumps(inline, separators=(',', ':')), original['id']))
                after = plain(dict(c.execute('SELECT * FROM items WHERE id=?', (original['id'],)).fetchone()))
                expected = deepcopy(original); expected['bundle'] = after['bundle']
                assert after == expected, 'Unexpected native item field mutation'
                if str(original['id']) in seeds:
                    seeds[str(original['id'])]['Bundle'] = deepcopy(inline)
                touched_items.add(str(original['id']))
            if old['bundle_row']:
                c.execute('UPDATE inf_asset_bundles SET version_content=?,version_stage=?,version_live=? WHERE bundle_id=?',
                    (e['version'], e['version'], e['version'], e['bundle_id']))
            registry[str(e['bundle_id'])] = e['bundle']
            target = Path(e['target'])
            publish(target, (payload_path.parent / Path(e['target']).name).read_bytes())
            changed.append(target)
            assert sha(target) == e['sha256']
        assert owned(c, 8) == inventory
        for path, value, touched in ((REGISTRY, registry, {str(i) for i in ids}), (SEEDS, seeds, touched_items)):
            publish(path, json.dumps(value, separators=(',', ':')).encode(), metadata[path]); changed.append(path)
            assert json.loads(path.read_text()) == value
            old = json.loads((backup / path.name).read_text())
            assert {k: v for k, v in value.items() if k not in touched} == {k: v for k, v in old.items() if k not in touched}
        c.commit(); committed = True
        result.update(status='applied', backup=str(backup), inventory_preserved=True,
            item_fields_preserved=True, catalog_entries_preserved=True, game_pid_unchanged=pid() == game_pid)
        (backup / 'result.json').write_text(json.dumps(result, indent=2))
    except Exception:
        if not committed:
            c.rollback()
            if 'backup' in locals():
                for path in (REGISTRY, SEEDS):
                    if path in changed:
                        publish(path, (backup / path.name).read_bytes(), metadata[path])
                for e in p['entries']:
                    path = Path(e['target'])
                    if path in changed and path.is_file() and sha(path) == e['sha256']:
                        path.unlink()
        raise
    finally:
        c.close()
        if stopped:
            subprocess.run(['systemctl', 'start', 'infinity-api'], check=True, timeout=60)
    result['services'] = services()
    assert pid() == game_pid
    return result


def verify(payload_path):
    p = json.loads(payload_path.read_text())
    query = ','.join(str(e['bundle_id']) for e in p['entries'])
    with urllib.request.urlopen('https://divinityarts.mooo.com/data/GetAssetBundlesByIDs?ids=' + query, timeout=30) as response:
        metadata = {b['ID']: b for b in json.load(response)}
    c = db.connect(); checks = []
    try:
        import assetwire
        old_entries = {e['bundle_id']: e for e in p['before']['entries']}
        for e in p['entries']:
            relative = Path(e['target']).relative_to('/opt/infinity/content')
            url = 'https://content.130-162-189-229.sslip.io/' + urllib.parse.quote(relative.as_posix(), safe='/')
            with urllib.request.urlopen(url, timeout=30) as response:
                binary = response.read()
            assert hashlib.sha256(binary).hexdigest() == e['sha256']
            b = metadata[e['bundle_id']]
            assert b['CRCPC'] == str(e['crc']) and all(b[k] == e['version'] for k in ('VersionContent', 'VersionStage', 'VersionLive'))
            old = old_entries[e['bundle_id']]
            for original in old['items']:
                raw = plain(dict(c.execute('SELECT * FROM items WHERE id=?', (original['id'],)).fetchone()))
                expected = deepcopy(original); expected['bundle'] = raw['bundle']; assert raw == expected
                wire = assetwire.normalize(db.item(c, original['id']))
                assert wire['Bundle']['ID'] == e['bundle_id'] and wire['Bundle']['VersionLive'] == e['version']
                assert wire['PrefabName'].casefold() == e['prefab'].casefold()
            checks.append(dict(bundle_id=e['bundle_id'], version=e['version'], filename=b.get('Filename') or b.get('FileName'),
                bytes=len(binary), sha256=e['sha256'], original_item_ids=[r['id'] for r in old['items']],
                public_delivery=True, api_metadata=True, normalized_item_wire=True))
    finally:
        c.close()
    return dict(status='passed', entries=checks, services=services(),
        proof_boundary='Published on native IDs/files and wire verified; player equip and appearance still require observation.')


def screen(ids):
    accepted,rejected=[],[]
    c=db.connect()
    try:
        for bid in ids:
            try:inspect([bid],connection=c)
            except (AssertionError,KeyError) as error:
                rejected.append(dict(bundle_id=bid,reason=str(error)))
            else:accepted.append(bid)
    finally:c.close()
    return dict(accepted_ids=accepted,rejected=rejected)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inspect', nargs='+', type=int)
    parser.add_argument('--screen', nargs='+', type=int)
    parser.add_argument('--payload', type=Path)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--verify', action='store_true')
    args = parser.parse_args()
    result = screen(args.screen) if args.screen else inspect(args.inspect) if args.inspect else verify(args.payload) if args.verify else apply(args.payload, args.apply)
    print(json.dumps(result, default=str))


if __name__ == '__main__':
    main()
