"""Guarded, one-time Cryo Chronomancer armor trial deployment. Defaults to read-only validation.

Run on the live host with its configured environment. Does not initialize schema,
restart the game, or alter existing items/equipment. An apply stops only the API
while publishing its registry, then starts it again.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

import db


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def dump(value):
    return json.dumps(value, indent=2, default=str)


def owned(c, cid):
    return [dict(r) for r in c.execute('SELECT * FROM char_items WHERE char_id=? ORDER BY char_item_id', (cid,)).fetchall()]


def publish(path, contents, metadata=None):
    path = Path(path)
    assert path.resolve().is_relative_to(Path('/opt/infinity'))
    path.parent.mkdir(parents=True, exist_ok=True)
    original = metadata or (path.stat() if path.exists() else None)
    fd, temp = tempfile.mkstemp(prefix='.flash-trial-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(contents)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temp, original.st_mode & 0o777 if original else 0o644)
        if original:
            os.chown(temp, original.st_uid, original.st_gid)
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--payload', type=Path, required=True)
    parser.add_argument('--bundle', type=Path, required=True)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    p = json.loads(args.payload.read_text())
    assert db.BACKEND == 'mysql'
    assert 2000000 <= p['item_id'] < 2100000 and 2000000 <= p['bundle_id'] < 2100000
    assert (p['char_id'], p['account_id'], p['char_name']) == (8, 8, 'redux')
    assert p['item']['ID'] == p['item_id'] and p['item']['Bundle']['ID'] == p['bundle_id']
    assert p['bundle']['ID'] == p['bundle_id']
    assert p['item']['ItemType'] == 22 and p['item']['EquipSpot'] == 7
    assert p['native_bundle_id'] == 49060 and p['native_item_id'] == 82191
    assert p['item']['MobileCompatibility'] == 1
    assert all(p['bundle'][key] == 1 and p['item']['Bundle'][key] == 1
               for key in ('VersionContent', 'VersionStage', 'VersionLive'))
    assert p['item']['Bundle']['CRCPC'] == p['bundle']['CRCPC']
    assert args.bundle.read_bytes().startswith(b'UnityFS')
    assert sha(args.bundle) == p['bundle_sha256']
    assert p['item']['Filename'] == 'ChronoFrostMage.swf'
    assert set(p['source_files']) == {'M', 'F'}
    for gender, source in p['source_files'].items():
        assert source['relative'] == 'classes/' + gender + '/ChronoFrostMage.swf'
        assert sha(Path('/var/www/swordshine/gamefiles') / source['relative']) == source['sha256']
    registry_path = Path('/opt/infinity/data/asset_bundles.json')
    seed_path = Path('/opt/infinity/data/items.json')
    target = Path(p['bundle_target'])
    bundle_relative = Path(p['bundle']['FileName'])
    assert bundle_relative.parent == Path('armors')
    assert bundle_relative.name == str(p['bundle_id']) + '_chronofrostmage_flash.unity3d'
    assert p['bundle']['Type'] == p['item']['Bundle']['Type'] == 'ARMOR'
    expected_target = Path('/opt/infinity/content/game/assetbundles/windows') / bundle_relative.parent / bundle_relative.stem / '1' / bundle_relative.name
    assert target == expected_target
    assert p['item']['Bundle']['Filename'] == bundle_relative.as_posix()
    native_id = str(p['native_bundle_id'])
    assert native_id != str(p['bundle_id'])
    assert not target.exists(), 'target already exists; do not replace or repeat a grant'
    assert sha(registry_path) == p['catalog_sha256'], 'catalog drift; repeat read-only preflight'
    assert sha(seed_path) == p['seed_sha256'], 'seed drift; repeat read-only preflight'
    registry = json.loads(registry_path.read_text())
    seeds = json.loads(seed_path.read_text())
    assert registry[native_id] == p['native_bundle']
    assert str(p['bundle_id']) not in registry and str(p['item_id']) not in seeds
    c = db.connect()
    engines = {r['table_name']:r['engine'] for r in c.execute(
        "SELECT table_name,engine FROM information_schema.tables WHERE table_schema=DATABASE() "
        "AND table_name IN ('items','users','users_items','inf_kv','inf_account_audit','inf_asset_bundles')").fetchall()}
    assert len(engines)==6 and set(engines.values())=={'InnoDB'}, 'all affected tables must support rollback'
    subprocess.run(['systemctl','is-active','--quiet','infinity-api'],check=True)
    char = dict(c.execute('SELECT id,account_id,name FROM characters WHERE id=?', (p['char_id'],)).fetchone())
    assert char == {'id':8,'account_id':8,'name':'redux'}
    assert c.execute('SELECT id FROM items WHERE id=?', (p['item_id'],)).fetchone() is None
    assert c.execute('SELECT bundle_id FROM inf_asset_bundles WHERE bundle_id=?', (p['bundle_id'],)).fetchone() is None
    assert c.execute('SELECT char_item_id FROM char_items WHERE char_id=? AND item_id=?', (8,p['item_id'])).fetchone() is None
    before_owned = owned(c, 8)
    before_default = dict(c.execute('SELECT * FROM items WHERE id=1').fetchone())
    before_native_item = dict(c.execute('SELECT * FROM items WHERE id=?', (82191,)).fetchone())
    assert db.item(c, 82191) == p['native_item']
    native = registry[native_id]
    result = {'status':'preflight_passed','apply_requested':args.apply,'item_id':p['item_id'],
              'bundle_id':p['bundle_id'],'character':char,'inventory_rows_before':len(before_owned),
              'bundle_sha256':sha(args.bundle),'game_pid_before':subprocess.check_output(
                  ['systemctl','show','infinity-game','-p','MainPID','--value'],text=True).strip()}
    if not args.apply:
        c.close()
        print(dump(result))
        return
    import support_manager
    # Complete backups and serialization before the short API interruption.
    backup = Path('/opt/infinity/backups') / (bundle_relative.stem.replace('_', '-') + datetime.now(timezone.utc).strftime('-%Y%m%dT%H%M%SZ'))
    backup.mkdir(mode=0o700)
    registry_stat, seed_stat = registry_path.stat(), seed_path.stat()
    shutil.copy2(registry_path, backup / 'asset_bundles.json')
    shutil.copy2(seed_path, backup / 'items.json')
    (backup / 'database-before.json').write_text(dump({'new_item_before':None,'new_bundle_before':None,
         'target_file_before':None,'character_identity':char,'redux_inventory':before_owned,
         'default_sword':before_default,'native_armor_item':before_native_item,'next_char_item_id':db.kv_get(c,'next_char_item_id')}))
    (backup / 'deployment_payload.json').write_text(dump(p))
    (backup / 'deploy_armor_trial.py').write_bytes(Path(__file__).read_bytes())
    registry[str(p['bundle_id'])] = p['bundle']
    seeds[str(p['item_id'])] = p['item']
    new_registry = json.dumps(registry, separators=(',', ':')).encode()
    new_seeds = json.dumps(seeds, separators=(',', ':')).encode()
    stopped = False
    committed = False
    changed = []
    try:
        c.rollback()  # end the read-only snapshot before taking the write lock
        c.execute('SELECT id FROM characters WHERE id=? FOR UPDATE', (8,)).fetchone()
        assert c.execute('SELECT id FROM items WHERE id=? FOR UPDATE', (p['item_id'],)).fetchone() is None
        assert owned(c,8) == before_owned, 'redux inventory changed during preflight'
        assert sha(registry_path) == p['catalog_sha256'] and sha(seed_path) == p['seed_sha256']
        stopped = True  # restoration is required even if the stop command fails or times out
        subprocess.run(['systemctl','stop','infinity-api'],check=True,timeout=30)
        assert sha(registry_path) == p['catalog_sha256'], 'API catalog changed while stopping'
        c.execute('INSERT INTO inf_asset_bundles(bundle_id,name,type,filename,version_content,version_stage,version_live,dependency_id) '
                  'VALUES(?,?,?,?,?,?,?,?)', (p['bundle_id'],p['bundle']['Name'],'ARMOR',p['bundle']['FileName'],1,1,1,0))
        db.store_item(c,p['item'])
        # Canonical storage does not map these two shared legacy-family columns.
        c.execute('UPDATE items SET Type=?,Equipment=? WHERE id=?', ('Armor','co',p['item_id']))
        new_item = db.item(c,p['item_id'])
        assert new_item['PrefabName'] == p['item']['PrefabName'] and new_item['Bundle']['ID'] == p['bundle_id']
        # Shared inventory also receives Flash-client inserts. Its native AUTO_INCREMENT
        # allocates safely across both writers; the Infinity kv counter may lag those IDs.
        inventory_id = c.execute("SELECT extra FROM information_schema.columns WHERE table_schema=DATABASE() "
                                 "AND table_name='users_items' AND column_name='id'").fetchone()
        assert 'auto_increment' in inventory_id['extra'].lower()
        inserted = c.execute('INSERT INTO users_items(UserID,ItemID,Quantity,Equipped,Bank,Wear,loot_id) '
                             'VALUES(?,?,?,?,?,?,?)', (8,p['item_id'],1,0,0,0,-1))
        char_item_id = int(inserted.lastrowid)
        support_manager.audit(c,8,8,'staff','Codex','staff_grant',item_id=p['item_id'],quantity=1,detail=p['reason'])
        after_owned = owned(c,8)
        new_owned = [r for r in after_owned if r['item_id']==p['item_id']]
        assert len(new_owned)==1 and new_owned[0]['quantity']==1 and not new_owned[0]['equipped'] and not new_owned[0]['banked']
        assert [r for r in after_owned if r['item_id']!=p['item_id']] == before_owned
        assert dict(c.execute('SELECT * FROM items WHERE id=1').fetchone()) == before_default
        assert dict(c.execute('SELECT * FROM items WHERE id=?', (82191,)).fetchone()) == before_native_item
        shared_bundle = dict(c.execute('SELECT * FROM inf_asset_bundles WHERE bundle_id=?', (p['bundle_id'],)).fetchone())
        assert shared_bundle['filename'] == p['bundle']['FileName'] and shared_bundle['version_live']==1
        publish(target,args.bundle.read_bytes()); changed.append(target)
        publish(registry_path,new_registry,registry_stat); changed.append(registry_path)
        publish(seed_path,new_seeds,seed_stat); changed.append(seed_path)
        assert sha(target)==p['bundle_sha256']
        check_registry=json.loads(registry_path.read_text()); check_seeds=json.loads(seed_path.read_text())
        assert check_registry.pop(str(p['bundle_id'])) == p['bundle']
        assert check_registry == json.loads((backup/'asset_bundles.json').read_text())
        assert check_seeds.pop(str(p['item_id'])) == p['item']
        assert check_seeds == json.loads((backup/'items.json').read_text())
        assert registry[native_id] == native
        c.commit()
        committed=True
        result.update(status='applied',backup=str(backup),char_item_id=char_item_id,
                      inventory_rows_after=len(after_owned),granted_row=new_owned[0],
                      existing_inventory_preserved=True,native_bundle_preserved=True,
                      item=new_item,content_path=str(target),transactional_engines=engines,
                      shared_bundle_catalog_registered=True)
        (backup/'result.json').write_text(dump(result))
    except Exception:
        if not committed:
            c.rollback()
            if registry_path in changed: publish(registry_path,(backup/'asset_bundles.json').read_bytes(),registry_stat)
            if seed_path in changed: publish(seed_path,(backup/'items.json').read_bytes(),seed_stat)
            if target in changed and sha(target)==p['bundle_sha256']: target.unlink()
        raise
    finally:
        c.close()
        if stopped:
            subprocess.run(['systemctl','start','infinity-api'],check=True,timeout=60)
    result['services'] = subprocess.check_output(['systemctl','is-active','infinity-api','infinity-game'],text=True).splitlines()
    result['game_pid_after'] = subprocess.check_output(['systemctl','show','infinity-game','-p','MainPID','--value'],text=True).strip()
    assert result['game_pid_before'] == result['game_pid_after'], 'game process unexpectedly changed'
    (backup/'result.json').write_text(dump(result))
    print(dump(result))


if __name__=='__main__':
    main()
