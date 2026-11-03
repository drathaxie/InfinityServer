"""Grant only verified converted native items to Redux; backups stay on host."""
import argparse
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import subprocess

import db
import game
import seed
from deploy_battlepet_live import publish

ROOT=Path('/opt/infinity')


def plain(value):return json.loads(json.dumps(value,default=str))


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--payload',type=Path,required=True);ap.add_argument('--apply',action='store_true');args=ap.parse_args()
    p=json.loads(args.payload.read_text());target=ROOT/'server/game.py';original=target.read_bytes();metadata=target.stat()
    assert hashlib.sha256(original).hexdigest()==p['game_sha256'],'Game source changed'
    text=original.decode();patched=text
    if 'def storage_capacity(' not in text:
        for edit in p['edits']:
            old,new=edit['old'],edit['new']
            if old not in patched and '\r\n' in patched:old,new=old.replace('\n','\r\n'),new.replace('\n','\r\n')
            assert patched.count(old)==1,'Capacity patch anchor changed';patched=patched.replace(old,new,1)
        compile(patched,str(target),'exec')
    else:
        normalized=text.replace('\r\n','\n')
        assert all(edit['new'] in normalized for edit in p['edits']),'Capacity implementation differs'
    c=db.connect();assert db.BACKEND=='mysql'
    char=dict(c.execute('SELECT id,account_id,name,class_id FROM characters WHERE id=8').fetchone())
    assert char['id']==char['account_id']==8 and char['name'].lower()=='redux'
    before=[dict(r) for r in c.execute('SELECT * FROM char_items WHERE char_id=8 ORDER BY char_item_id').fetchall()]
    owned={r['item_id'] for r in before};roster=p['roster'];assert len(roster)==len({r['item_id'] for r in roster}) and 1<=len(roster)<=10000
    native_bundles={r['bundle_id'] for r in roster if 0<r['item_id']<2000000}
    missing=[];skipped=[]
    for row in roster:
        assert 'caladbolg' not in (row['name']+' '+row['filename']).lower()
        if row['item_id']>=2000000:
            # Custom items (e.g. Daimyo Commander on Dragonslayer's bundle) can reuse a
            # converted native bundle. They are not conversions, so they are never granted.
            assert row['bundle_id'] in native_bundles,'Custom roster item does not share a converted native bundle'
            skipped.append(row['item_id']);continue
        assert row['item_id']>0
        item=db.item(c,row['item_id']);assert item and item['Name']==row['name']
        b=item.get('Bundle') or {};assert b.get('ID')==row['bundle_id'] and int(b.get('VersionLive',0))>=row['version']
        assert item['PrefabName'].casefold()==row['prefab'].casefold()
        if row['item_id'] not in owned:missing.append((row['item_id'],seed.CLASS_CP_MAX if game._item_is_class(item) else 1))
    key='inventory_capacity:8';old_capacity=db.kv_get(c,key)
    settings=json.loads(old_capacity or '{}');assert isinstance(settings,dict)
    for name in ('BagSlots','BankSlots'):settings[name]=max(4096,int(settings.get(name,0)))
    assert max(settings['BagSlots'],settings['BankSlots'])<=20000
    result=dict(status='preflight_passed',roster_items=len(roster),skipped_custom_items=skipped,already_owned=len(roster)-len(skipped)-len(missing),missing=len(missing),capacity=settings,source_patch=patched!=text)
    if not args.apply:print(json.dumps(result));c.close();return
    backup=ROOT/'backups'/datetime.now(timezone.utc).strftime('redux-converted-grant-%Y%m%dT%H%M%SZ');backup.mkdir(mode=0o700)
    (backup/'before.json').write_text(json.dumps(plain(dict(character=char,inventory=before,capacity=old_capacity)),indent=2))
    (backup/'game.py').write_bytes(original);(backup/'payload.json').write_bytes(args.payload.read_bytes())
    inserted=[];committed=False;stopped=False;changed=False
    try:
        c.rollback()
        if patched!=text:
            stopped=True;subprocess.run(['systemctl','stop','infinity-api','infinity-game'],check=True,timeout=60)
        current=dict(c.execute('SELECT id,account_id,name,class_id FROM characters WHERE id=8 FOR UPDATE').fetchone());assert current==char
        assert [dict(r) for r in c.execute('SELECT * FROM char_items WHERE char_id=8 ORDER BY char_item_id FOR UPDATE').fetchall()]==before
        assert db.kv_get(c,key)==old_capacity
        for iid,quantity in missing:
            cursor=c.execute('INSERT INTO users_items(UserID,ItemID,Quantity,Equipped,Bank,Wear,loot_id) VALUES(?,?,?,?,?,?,?)',(8,iid,quantity,0,0,0,-1));inserted.append(int(cursor.lastrowid))
        db.kv_set(c,key,json.dumps(settings))
        if patched!=text:
            assert target.read_bytes()==original
            publish(target,patched.encode(),metadata);changed=True
        c.commit();committed=True
        if stopped:
            subprocess.run(['systemctl','start','infinity-api','infinity-game'],check=True,timeout=60);stopped=False
        fresh=[dict(r) for r in c.execute('SELECT * FROM char_items WHERE char_id=8 ORDER BY char_item_id').fetchall()]
        assert plain([r for r in fresh if r['char_item_id'] not in inserted])==plain(before)
        assert {r['item_id'] for r in fresh}>={r['item_id'] for r in roster if r['item_id'] not in skipped}
        assert all(not r['equipped'] and not r['banked'] for r in fresh if r['char_item_id'] in inserted)
        full_char=c.execute('SELECT * FROM characters WHERE id=8').fetchone();wire=game.build_init_player(c,full_char)
        # This process imported the previous game module. Its source is reloaded
        # only in restarted services; verify the new function in a fresh process.
        wire_bytes=len(json.dumps(wire,separators=(',',':')).encode());assert wire_bytes<8*1024*1024
        services=subprocess.check_output(['systemctl','is-active','infinity-api','infinity-game'],text=True).splitlines();assert services==['active','active']
        result.update(status='applied',added=len(inserted),backup=str(backup),existing_inventory_preserved=True,equipment_preserved=True,wire_bytes=wire_bytes,services=services)
        (backup/'result.json').write_text(json.dumps(result,indent=2));print(json.dumps(result))
    except Exception:
        if not committed:c.rollback()
        else:
            for iid in inserted:c.execute('DELETE FROM char_items WHERE char_id=8 AND char_item_id=?',(iid,))
            if old_capacity is None:c.execute('DELETE FROM kv WHERE k=?',(key,))
            else:db.kv_set(c,key,old_capacity)
            c.commit()
        if changed:
            subprocess.run(['systemctl','stop','infinity-api','infinity-game'],timeout=60);stopped=True;publish(target,original,metadata)
        raise
    finally:
        c.close()
        if stopped:subprocess.run(['systemctl','start','infinity-api','infinity-game'],check=True,timeout=60)


if __name__=='__main__':main()
