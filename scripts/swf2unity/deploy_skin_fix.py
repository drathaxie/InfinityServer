"""Versioned repair of the two existing armor trials; never grants or replaces inventory."""
import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import db


def sha(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def dump(v): return json.dumps(v,indent=2,default=str)
REGISTRY = Path('/opt/infinity/data/asset_bundles.json')
SEEDS = Path('/opt/infinity/data/items.json')


def inspect(ids):
    c=db.connect()
    try:
        registry=json.loads(REGISTRY.read_text()); seeds=json.loads(SEEDS.read_text())
        return dict(catalog_sha256=sha(REGISTRY),seed_sha256=sha(SEEDS),entries=[
            dict(id=i,raw=dict(c.execute('SELECT * FROM items WHERE id=?',(i,)).fetchone()),
                 bundle_row=dict(c.execute('SELECT * FROM inf_asset_bundles WHERE bundle_id=?',(i,)).fetchone()),
                 bundle=registry[str(i)],seed=seeds[str(i)]) for i in ids])
    finally:c.close()


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--inspect',action='store_true');ap.add_argument('--payload',type=Path);ap.add_argument('--apply',action='store_true')
    ap.add_argument('--ids', nargs='+', type=int, default=[2000002,2000005])
    args=ap.parse_args()
    assert len(args.ids)==2 and len(set(args.ids))==2 and all(2000000<=i<2100000 for i in args.ids)
    if args.inspect: print(dump(inspect(args.ids))); return
    from deploy_pet_trial import publish, owned
    p=json.loads(args.payload.read_text()); before=p['before']; entries=p['entries']
    assert {e['id'] for e in entries}==set(args.ids) and len(entries)==2
    assert json.loads(dump(inspect(args.ids)))==before, 'catalog or trial drift; refresh preflight'
    c=db.connect(); changed=[]; stopped=committed=False
    registry=json.loads(REGISTRY.read_text()); seeds=json.loads(SEEDS.read_text())
    old_entries={e['id']:e for e in before['entries']}
    for e in entries:
        old=old_entries[e['id']]; new=e['bundle']; version=e['version']
        assert new['ID']==e['id'] and new['FileName']==old['bundle']['FileName']
        expected=deepcopy(old['bundle'])
        expected.update({k:version for k in ('Version','VersionContent','VersionStage','VersionLive','_cdnvL')})
        expected['CRCPC']=str(e['crc']); assert new==expected
        assert version==max(int(old['bundle'].get(k,0)) for k in ('Version','VersionContent','VersionStage','VersionLive'))+1
        relative=Path(new['FileName']); target=Path('/opt/infinity/content/game/assetbundles/windows')/relative.parent/relative.stem/str(version)/relative.name
        assert str(target)==e['target'] and not target.exists()
        binary=args.payload.parent/relative.name
        assert binary.read_bytes().startswith(b'UnityFS') and sha(binary)==e['sha256']
    pid=subprocess.check_output(['systemctl','show','infinity-game','-p','MainPID','--value'],text=True).strip()
    assert subprocess.check_output(['systemctl','is-active','infinity-api','infinity-game'],text=True).splitlines()==['active','active']
    result=dict(status='preflight_passed',ids=[e['id'] for e in entries])
    try:
        if not args.apply: print(dump(result)); return
        c.execute('SELECT id FROM characters WHERE id=? FOR UPDATE',(8,)).fetchone()
        inventory=owned(c,8)
        for e in entries:
            raw=dict(c.execute('SELECT * FROM items WHERE id=? FOR UPDATE',(e['id'],)).fetchone())
            row=dict(c.execute('SELECT * FROM inf_asset_bundles WHERE bundle_id=? FOR UPDATE',(e['id'],)).fetchone())
            assert json.loads(dump(raw))==old_entries[e['id']]['raw'] and json.loads(dump(row))==old_entries[e['id']]['bundle_row']
        backup=Path('/opt/infinity/backups')/datetime.now(timezone.utc).strftime('armor-repair-%Y%m%dT%H%M%SZ')
        backup.mkdir(mode=0o700)
        for path in (REGISTRY,SEEDS): shutil.copy2(path,backup/path.name)
        (backup/'database-before.json').write_text(dump(dict(before=before,inventory=inventory)))
        shutil.copy2(args.payload,backup/'payload.json')
        assert sha(REGISTRY)==before['catalog_sha256'] and sha(SEEDS)==before['seed_sha256']
        stopped=True;subprocess.run(['systemctl','stop','infinity-api'],check=True,timeout=30)
        for e in entries:
            old=old_entries[e['id']]; version=e['version']; inline=json.loads(old['raw']['bundle'])
            inline.update({k:version for k in ('Version','VersionContent','VersionStage','VersionLive')})
            inline['CRCPC']=str(e['crc'])
            c.execute('UPDATE items SET bundle=? WHERE id=?',(json.dumps(inline,separators=(',',':')),e['id']))
            c.execute('UPDATE inf_asset_bundles SET version_content=?,version_stage=?,version_live=? WHERE bundle_id=?',
                      (version,version,version,e['id']))
            after=dict(c.execute('SELECT * FROM items WHERE id=?',(e['id'],)).fetchone())
            original=deepcopy(old['raw']); original['bundle']=after['bundle'];assert json.loads(dump(after))==original
            assert db.item(c,e['id'])['Bundle']['CRCPC']==str(e['crc'])
            registry[str(e['id'])]=e['bundle']
            seeds[str(e['id'])]['Bundle']=deepcopy(inline)
            target=Path(e['target']);publish(target,(args.payload.parent/Path(e['bundle']['FileName']).name).read_bytes());changed.append(target)
            assert sha(target)==e['sha256']
        assert owned(c,8)==inventory
        for path,value in ((REGISTRY,registry),(SEEDS,seeds)):
            publish(path,json.dumps(value,separators=(',',':')).encode());changed.append(path)
            assert json.loads(path.read_text())==value
            old=json.loads((backup/path.name).read_text())
            repaired={str(i) for i in args.ids}
            assert {k:v for k,v in value.items() if k not in repaired}=={k:v for k,v in old.items() if k not in repaired}
        c.commit();committed=True
        result.update(status='applied',backup=str(backup),inventory_preserved=True,game_pid=pid,entries=entries)
    except Exception:
        if not committed:
            c.rollback()
            for path in (REGISTRY,SEEDS):
                if path in changed: publish(path,(backup/path.name).read_bytes())
            for e in entries:
                path=Path(e['target'])
                if path in changed and sha(path)==e['sha256']:path.unlink()
        raise
    finally:
        c.close()
        if stopped:subprocess.run(['systemctl','start','infinity-api'],check=True,timeout=60)
    result['services']=subprocess.check_output(['systemctl','is-active','infinity-api','infinity-game'],text=True).splitlines()
    assert result['services']==['active','active'] and pid==subprocess.check_output(['systemctl','show','infinity-game','-p','MainPID','--value'],text=True).strip()
    (backup/'result.json').write_text(dump(result));print(dump(result))


if __name__=='__main__':main()
