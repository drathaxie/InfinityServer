"""Guarded additive battlepet release. Backups stay on the live host.

Dry run validates exact remote hashes, anchored code edits and unused IDs.
Apply briefly stops API/game to publish the coherent code/catalog/class release.
"""
import argparse
from datetime import datetime,timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time

import db
import forge
import seed

ROOT=Path('/opt/infinity')


def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def dump(v):return json.dumps(v,indent=2,default=str)
def publish(path,blob,metadata=None):
    assert path.resolve().is_relative_to(ROOT)
    path.parent.mkdir(parents=True,exist_ok=True)
    metadata=metadata or (path.stat() if path.exists() else None)
    fd,name=tempfile.mkstemp(prefix='.battlepet-',dir=path.parent)
    try:
        with os.fdopen(fd,'wb') as f:f.write(blob);f.flush();os.fsync(f.fileno())
        os.chmod(name,metadata.st_mode&0o777 if metadata else 0o644)
        if metadata:os.chown(name,metadata.st_uid,metadata.st_gid)
        os.replace(name,path)
    finally:
        if os.path.exists(name):os.unlink(name)


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--folder',type=Path,required=True);ap.add_argument('--apply',action='store_true');args=ap.parse_args()
    p=json.loads((args.folder/'deployment_payload.json').read_text());pre=p['preflight'];c=db.connect();assert db.BACKEND=='mysql'
    cid=pre['class_id'];iid=p['class_item']['ID'];pid=p['pet_item']['ID'];bid=p['bundle']['ID']
    assert (cid,iid,pid,bid)==(2000000,2000029,2000030,2000029), 'Refresh the release script allocation guard if another trial consumed these IDs'
    char=dict(c.execute('SELECT id,account_id,name,class_id FROM characters WHERE id=8').fetchone());assert char['id']==char['account_id']==8 and char['name'].lower()=='redux'
    for table,col,value in [('classes','class_id',cid),('items','id',iid),('items','id',pid),('inf_asset_bundles','bundle_id',bid)]:
        assert c.execute(f'SELECT {col} FROM {table} WHERE {col}=?',(value,)).fetchone() is None,(table,value)
    files={};originals={};metadata={}
    for name,info in pre['sources'].items():
        path=ROOT/name;assert sha(path)==info['sha256'],f'Source changed: {name}'
        original=path.read_bytes();text=original.decode('utf-8')
        for edit in [e for e in p['edits'] if e['file']==name]:
            old,new=edit['old'],edit['new']
            if text.count(old)==0 and '\r\n' in text:
                old,new=old.replace('\n','\r\n'),new.replace('\n','\r\n')
            assert text.count(old)==1,(name,'anchor mismatch',old[:90]);text=text.replace(old,new,1)
        if text.encode()!=original:
            compile(text,name,'exec');files[path]=text.encode()
    module=args.folder/'battlepets.py';assert sha(module)==p['module_sha256'];compile(module.read_text(),'battlepets.py','exec')
    module_target=ROOT/'server/battlepets.py';assert not module_target.exists();files[module_target]=module.read_bytes()
    registry_path=ROOT/'data/asset_bundles.json';seeds_path=ROOT/'data/items.json';loader_path=ROOT/'data/mod/InfinityLoader.dll';hash_path=loader_path.with_suffix('.dll.sha256')
    assert sha(registry_path)==pre['registry_sha256'] and sha(seeds_path)==pre['seeds_sha256'],'Catalog changed; refresh metadata preflight'
    assert sha(loader_path)==pre['loader_sha256'] and hash_path.read_text().strip().lower()==pre['loader_sha256']
    registry=json.loads(registry_path.read_text());seeds=json.loads(seeds_path.read_text());assert str(bid) not in registry and str(iid) not in seeds and str(pid) not in seeds
    registry[str(bid)]=p['bundle'];seeds[str(iid)]=p['class_item'];seeds[str(pid)]=p['pet_item']
    files[registry_path]=json.dumps(registry,separators=(',',':')).encode();files[seeds_path]=json.dumps(seeds,separators=(',',':')).encode()
    bundle=args.folder/'daimyo_battlepet.unity3d';loader=args.folder/'InfinityLoader.dll'
    assert sha(bundle)==p['bundle_sha256'] and sha(loader)==p['loader_sha256']
    bundle_target=ROOT/'content/game/assetbundles/windows/npcs/daimyo_battlepet/1/daimyo_battlepet.unity3d';assert not bundle_target.exists()
    files[bundle_target]=bundle.read_bytes();files[loader_path]=loader.read_bytes();files[hash_path]=(p['loader_sha256']+'\n').encode()
    caps_path=ROOT/'data/battlepet_capabilities.json';caps=json.loads(caps_path.read_text()) if caps_path.exists() else {};assert not(set(caps)&set(p['capabilities']));caps.update(p['capabilities']);files[caps_path]=dump(caps).encode()
    result=dict(status='preflight_passed',class_id=cid,class_item_id=iid,pet_item_id=pid,bundle_id=bid,code_files=sum(str(x).endswith('.py') for x in files),source_mean=p['source_mean'],gpu_mean=p['gpu_mean'])
    if not args.apply:print(dump(result));c.close();return
    backup=ROOT/'backups'/datetime.now(timezone.utc).strftime('battlepet-live-%Y%m%dT%H%M%SZ');backup.mkdir(mode=0o700)
    before_owned=[dict(r) for r in c.execute('SELECT * FROM char_items WHERE char_id=8 ORDER BY char_item_id').fetchall()]
    original_pet=dict(c.execute('SELECT * FROM items WHERE id=6673').fetchone())
    original_class=dict(c.execute('SELECT * FROM classes WHERE class_id=1932').fetchone())
    before=dict(character=char,inventory=before_owned,original_pet=original_pet,base_class=original_class)
    (backup/'database-before.json').write_text(dump(before));(backup/'deployment_payload.json').write_text(dump(p));(backup/'deployer.py').write_bytes(Path(__file__).read_bytes())
    for path in files:
        originals[path]=path.read_bytes() if path.exists() else None;metadata[path]=path.stat() if path.exists() else None
        if path.exists():
            save=backup/'files'/path.relative_to(ROOT);save.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(path,save)
    committed=False;changed=[];sids=[];stopped=False
    try:
        stopped=True;subprocess.run(['systemctl','stop','infinity-api','infinity-game'],check=True,timeout=60)
        # MySQL repeatable-read must not hide inventory changes since preflight.
        c.rollback()
        assert dict(c.execute('SELECT id,account_id,name,class_id FROM characters WHERE id=8').fetchone())==char
        for name,info in pre['sources'].items():assert sha(ROOT/name)==info['sha256']
        assert sha(registry_path)==pre['registry_sha256'] and sha(seeds_path)==pre['seeds_sha256']
        assert [dict(r) for r in c.execute('SELECT * FROM char_items WHERE char_id=8 ORDER BY char_item_id').fetchall()]==before_owned
        c.execute('INSERT INTO inf_asset_bundles(bundle_id,name,type,filename,version_content,version_stage,version_live,dependency_id) VALUES(?,?,?,?,?,?,?,?)',(bid,p['bundle']['Name'],'NPC',p['bundle']['FileName'],1,1,1,0))
        for item,kind,equipment in [(p['class_item'],'Class','ar'),(p['pet_item'],'Pet','pe')]:
            db.store_item(c,item);c.execute('UPDATE items SET Type=?,Equipment=? WHERE id=?',(kind,equipment,item['ID']))
        raw={'battlepet':{'pet_item_id':pid},'description':'Commands an Armored Daimyo battlepet.'}
        resource={'model':'mana','ResourceColor':255,'MaxRP':100,'Threshold':-1,'ThresholdColor':-1}
        c.execute('INSERT INTO classes(class_id,name,bundle,rig,resource,raw) VALUES(?,?,?,?,?,?)',(cid,'Daimyo Commander','',dump(p['class_rig']),dump(resource),dump(raw)))
        for sk in p['skills']:
            sid=forge._next_skill_id(c);sids.append(sid)
            forge._write_skill(c,sid,sk['action'],sk['name'],sk['description'],sk['icon'],sk['slot'],dump(sk['data']),dump(sk['forge']))
            forge._set_class_slot(c,cid,sk['slot'],sid);c.execute('UPDATE skills SET mana=? WHERE skill_id=?',(sk['mana'],sid))
        replaced=[]
        for row in before_owned:
            if row['equipped'] and not row['banked']:
                item=db.item(c,row['item_id'])
                if item and item.get('EquipSpot') in (5,6):
                    c.execute('UPDATE char_items SET equipped=0 WHERE char_id=8 AND char_item_id=?',(row['char_item_id'],));replaced.append(row['char_item_id'])
        for item_id in [iid,pid]:
            quantity=seed.CLASS_CP_MAX if item_id==iid else 1
            c.execute('INSERT INTO users_items(UserID,ItemID,Quantity,Equipped,Bank,Wear,loot_id) VALUES(?,?,?,?,?,?,?)',(8,item_id,quantity,1,0,0,-1))
        c.execute('UPDATE characters SET class_id=? WHERE id=8 AND account_id=8',(cid,))
        after=[dict(r) for r in c.execute('SELECT * FROM char_items WHERE char_id=8 ORDER BY char_item_id').fetchall()]
        prior=[r for r in after if r['item_id'] not in (iid,pid)];expected=[dict(r,equipped=0) if r['char_item_id'] in replaced else r for r in before_owned]
        assert prior==expected,'Unrelated inventory changed'
        assert len(after)==len(before_owned)+2 and len(sids)==5
        assert dict(c.execute('SELECT * FROM items WHERE id=6673').fetchone())==original_pet
        assert dict(c.execute('SELECT * FROM classes WHERE class_id=1932').fetchone())==original_class
        for path,blob in files.items():publish(path,blob,metadata[path]);changed.append(path)
        c.commit();committed=True
        result.update(status='applied',backup=str(backup),skill_ids=sids,inventory_rows_added=2,equipped=True,original_records_preserved=True,loader_sha256=p['loader_sha256'])
        subprocess.run(['systemctl','start','infinity-api','infinity-game'],check=True,timeout=60);stopped=False
        for _ in range(20):
            services=subprocess.run(['systemctl','is-active','infinity-api','infinity-game'],text=True,capture_output=True,check=False).stdout.splitlines()
            if services==['active','active']:break
            time.sleep(.5)
        assert services==['active','active'];result['services']=services
        (backup/'result.json').write_text(dump(result));print(dump(result))
    except Exception:
        subprocess.run(['systemctl','stop','infinity-api','infinity-game'],timeout=60)
        if not committed:c.rollback()
        else:
            for sid in sids:c.execute('DELETE FROM class_skills WHERE class_id=? AND skill_id=?',(cid,sid));c.execute('DELETE FROM skills WHERE skill_id=?',(sid,))
            c.execute('DELETE FROM classes WHERE class_id=?',(cid,));c.execute('DELETE FROM char_items WHERE char_id=8 AND item_id IN (?,?)',(iid,pid));c.execute('DELETE FROM items WHERE id IN (?,?)',(iid,pid));c.execute('DELETE FROM inf_asset_bundles WHERE bundle_id=?',(bid,))
            c.execute('UPDATE characters SET class_id=? WHERE id=8 AND class_id=?',(char['class_id'],cid))
            for row in before_owned:c.execute('UPDATE char_items SET equipped=? WHERE char_id=8 AND char_item_id=?',(row['equipped'],row['char_item_id']))
            c.commit()
        for path in reversed(changed):
            if originals[path] is None:path.unlink(missing_ok=True)
            else:publish(path,originals[path],metadata[path])
        subprocess.run(['systemctl','start','infinity-api','infinity-game'],check=True,timeout=60);stopped=False
        raise
    finally:
        c.close()
        if stopped:subprocess.run(['systemctl','start','infinity-api','infinity-game'],timeout=60)

if __name__=='__main__':main()
