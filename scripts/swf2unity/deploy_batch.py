"""Guarded two-item conversion test deployment. Default is a dry run; --apply grants once."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess

import db
from deploy_pet_trial import publish, owned


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def dump(value):
    return json.dumps(value, indent=2, default=str)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--payload', type=Path, required=True)
    ap.add_argument('--apply', action='store_true')
    args = ap.parse_args()
    p = json.loads(args.payload.read_text())
    assert db.BACKEND == 'mysql' and re.fullmatch(r'batch\d{2}', p['batch'])
    assert p['character'] == dict(id=8, account_id=8, name='redux')
    entries = p['entries']
    assert 1 <= len(entries) <= 12 and len({e['source_id'] for e in entries}) == len(entries)
    assert len({e['item']['ID'] for e in entries}) == len({e['bundle']['ID'] for e in entries}) == len(entries)
    registry_path = Path('/opt/infinity/data/asset_bundles.json')
    seed_path = Path('/opt/infinity/data/items.json')
    assert sha(registry_path) == p['catalog_sha256'] and sha(seed_path) == p['seed_sha256'], 'catalog drift; refresh preflight'
    registry = json.loads(registry_path.read_text())
    seeds = json.loads(seed_path.read_text())
    for e in entries:
        item, bundle = e['item'], e['bundle']
        assert 2000000 <= item['ID'] < 2100000 and 2000000 <= bundle['ID'] < 2100000
        assert item['Bundle']['ID'] == bundle['ID']
        assert all(bundle[k] == item['Bundle'][k] == 1 for k in ('VersionLive', 'VersionStage', 'VersionContent'))
        assert item['Bundle']['CRCPC'] == bundle['CRCPC'] and item['MobileCompatibility'] == 1
        expected = {'Sword': (6, 2, 'ITEM', 'items/swords'), 'Polearm': (20, 2, 'ITEM', 'items/polearms'),
                    'Armor': (22, 7, 'ARMOR', 'armors'), 'Class': (22, 7, 'ARMOR', 'armors'),
                    'Helm': (12, 3, 'ITEM', 'items/helms')}[e['original']['Type']]
        assert (item['ItemType'], item['EquipSpot'], bundle['Type']) == expected[:3]
        relative = Path(bundle['FileName'])
        assert relative.parent == Path(expected[3]) and re.fullmatch(r'[a-z]+_' + p['batch'] + r'_flash_trial\.unity3d', relative.name)
        assert item['Bundle']['Filename'] == relative.as_posix()
        target = Path('/opt/infinity/content/game/assetbundles/windows') / relative.parent / relative.stem / '1' / relative.name
        assert str(target) == e['bundle_target'] and not target.exists()
        binary = args.payload.parent / relative.name
        assert binary.read_bytes().startswith(b'UnityFS') and sha(binary) == e['bundle_sha256']
        assert str(bundle['ID']) not in registry and str(item['ID']) not in seeds
        for s in e['sources']:
            assert '..' not in s['relative'] and sha(Path('/var/www/swordshine/gamefiles') / s['relative']) == s['sha256']
    c = db.connect()
    try:
        engines = {r['table_name']: r['engine'] for r in c.execute(
            "SELECT table_name,engine FROM information_schema.tables WHERE table_schema=DATABASE() "
            "AND table_name IN ('items','users','users_items','inf_kv','inf_account_audit','inf_asset_bundles')").fetchall()}
        assert len(engines) == 6 and set(engines.values()) == {'InnoDB'}
        assert dict(c.execute('SELECT id,account_id,name FROM characters WHERE id=?', (8,)).fetchone()) == p['character']
        for e in entries:
            assert not c.execute('SELECT id FROM items WHERE id=?', (e['item']['ID'],)).fetchone()
            assert not c.execute('SELECT bundle_id FROM inf_asset_bundles WHERE bundle_id=?', (e['bundle']['ID'],)).fetchone()
            original = dict(c.execute('SELECT * FROM items WHERE id=?', (e['source_id'],)).fetchone())
            assert json.loads(dump(original)) == e['original']
        before = owned(c, 8)
        pid = subprocess.check_output(['systemctl', 'show', 'infinity-game', '-p', 'MainPID', '--value'], text=True).strip()
        services = subprocess.check_output(['systemctl', 'is-active', 'infinity-api', 'infinity-game'], text=True).splitlines()
        assert services == ['active', 'active']
        result = dict(status='preflight_passed', items=[e['item']['ID'] for e in entries], game_pid_before=pid,
                      inventory_rows_before=len(before), transactional_engines=engines)
        if not args.apply:
            print(dump(result))
            return
        import support_manager
        backup = Path('/opt/infinity/backups') / datetime.now(timezone.utc).strftime('conversion-' + p['batch'] + '-%Y%m%dT%H%M%SZ')
        backup.mkdir(mode=0o700)
        registry_stat, seed_stat = registry_path.stat(), seed_path.stat()
        shutil.copy2(registry_path, backup / registry_path.name)
        shutil.copy2(seed_path, backup / seed_path.name)
        (backup / 'database-before.json').write_text(dump(dict(character=p['character'], inventory=before,
            originals={str(e['source_id']): e['original'] for e in entries})))
        shutil.copy2(args.payload, backup / 'payload.json')
        shutil.copy2(Path(__file__), backup / 'deploy_batch.py')
        shutil.copy2(Path(__file__).with_name('deploy_pet_trial.py'), backup / 'deploy_pet_trial.py')
        changed = []
        stopped = committed = False
        grants = []
        try:
            c.rollback()
            c.execute('SELECT id FROM characters WHERE id=? FOR UPDATE', (8,)).fetchone()
            assert owned(c, 8) == before, 'inventory changed during preflight'
            for e in entries:
                original = dict(c.execute('SELECT * FROM items WHERE id=? FOR UPDATE', (e['source_id'],)).fetchone())
                assert json.loads(dump(original)) == e['original']
                assert not c.execute('SELECT id FROM items WHERE id=? FOR UPDATE', (e['item']['ID'],)).fetchone()
                assert not c.execute('SELECT bundle_id FROM inf_asset_bundles WHERE bundle_id=? FOR UPDATE', (e['bundle']['ID'],)).fetchone()
            assert sha(registry_path) == p['catalog_sha256'] and sha(seed_path) == p['seed_sha256']
            stopped = True
            subprocess.run(['systemctl', 'stop', 'infinity-api'], check=True, timeout=30)
            assert sha(registry_path) == p['catalog_sha256'] and sha(seed_path) == p['seed_sha256']
            for e in entries:
                item, bundle = e['item'], e['bundle']
                c.execute('INSERT INTO inf_asset_bundles(bundle_id,name,type,filename,version_content,version_stage,version_live,dependency_id) '
                          'VALUES(?,?,?,?,?,?,?,?)', (bundle['ID'], bundle['Name'], bundle['Type'], bundle['FileName'], 1, 1, 1, 0))
                db.store_item(c, item)
                legacy = ('Armor', 'co') if item['ItemType'] == 22 else (e['original']['Type'], 'Weapon')
                c.execute('UPDATE items SET Type=?,Equipment=? WHERE id=?', (*legacy, item['ID']))
                check = db.item(c, item['ID'])
                assert (check['PrefabName'], check['Bundle']['ID'], check['ItemType'], check['EquipSpot']) == (
                    item['PrefabName'], bundle['ID'], item['ItemType'], item['EquipSpot'])
                extra = c.execute("SELECT extra FROM information_schema.columns WHERE table_schema=DATABASE() "
                                  "AND table_name='users_items' AND column_name='id'").fetchone()
                assert 'auto_increment' in extra['extra'].lower()
                inserted = c.execute('INSERT INTO users_items(UserID,ItemID,Quantity,Equipped,Bank,Wear,loot_id) VALUES(?,?,?,?,?,?,?)',
                                     (8, item['ID'], 1, 0, 0, 0, -1))
                support_manager.audit(c, 8, 8, 'staff', 'Codex', 'staff_grant', item_id=item['ID'], quantity=1, detail=p['reason'])
                grants.append(dict(item_id=item['ID'], char_item_id=int(inserted.lastrowid)))
                registry[str(bundle['ID'])] = bundle
                seeds[str(item['ID'])] = item
                target = Path(e['bundle_target'])
                publish(target, (args.payload.parent / Path(bundle['FileName']).name).read_bytes())
                changed.append(target)
                assert sha(target) == e['bundle_sha256']
            ids = {e['item']['ID'] for e in entries}
            after = owned(c, 8)
            assert [r for r in after if r['item_id'] not in ids] == before
            new = [r for r in after if r['item_id'] in ids]
            assert len(new) == len(entries) and all(r['quantity'] == 1 and not r['equipped'] and not r['banked'] for r in new)
            for e in entries:
                assert json.loads(dump(dict(c.execute('SELECT * FROM items WHERE id=?', (e['source_id'],)).fetchone()))) == e['original']
            publish(registry_path, json.dumps(registry, separators=(',', ':')).encode(), registry_stat)
            changed.append(registry_path)
            publish(seed_path, json.dumps(seeds, separators=(',', ':')).encode(), seed_stat)
            changed.append(seed_path)
            check_registry = json.loads(registry_path.read_text())
            check_seeds = json.loads(seed_path.read_text())
            for e in entries:
                assert check_registry.pop(str(e['bundle']['ID'])) == e['bundle']
                assert check_seeds.pop(str(e['item']['ID'])) == e['item']
            assert check_registry == json.loads((backup / registry_path.name).read_text())
            assert check_seeds == json.loads((backup / seed_path.name).read_text())
            c.commit()
            committed = True
            result.update(status='applied', backup=str(backup), grants=grants, inventory_rows_after=len(after),
                existing_inventory_preserved=True, source_items_preserved=True, existing_catalogs_preserved=True)
        except Exception:
            if not committed:
                c.rollback()
                if registry_path in changed: publish(registry_path, (backup / registry_path.name).read_bytes(), registry_stat)
                if seed_path in changed: publish(seed_path, (backup / seed_path.name).read_bytes(), seed_stat)
                for e in entries:
                    target = Path(e['bundle_target'])
                    if target in changed and sha(target) == e['bundle_sha256']: target.unlink()
            raise
        finally:
            if stopped: subprocess.run(['systemctl', 'start', 'infinity-api'], check=True, timeout=60)
        result['services'] = subprocess.check_output(['systemctl', 'is-active', 'infinity-api', 'infinity-game'], text=True).splitlines()
        result['game_pid_after'] = subprocess.check_output(['systemctl', 'show', 'infinity-game', '-p', 'MainPID', '--value'], text=True).strip()
        assert result['services'] == ['active', 'active'] and result['game_pid_after'] == pid
        (backup / 'result.json').write_text(dump(result))
        print(dump(result))
    finally:
        c.close()


if __name__ == '__main__':
    main()
