"""Audit Redux-owned item art against verified publication metadata, not account data."""
from collections import Counter
import json
from pathlib import Path
from trial_host import ROOT,server_script

OUT=ROOT/'outputs/flash_converter_trial/redux_inventory'


def claims():
    result={}
    def add(bundle,sha,proof):
        if not bundle or not sha:return
        bid=int(bundle['ID']);version=max(int(bundle.get(k) or 0) for k in ('Version','VersionLive','VersionContent','VersionStage'))
        candidate=dict(bundle_id=bid,version=version,crc=str(bundle.get('CRCPC','')),sha256=sha,proof=str(proof.relative_to(ROOT)))
        if bid not in result or version>=result[bid]['version']:result[bid]=candidate
    base=ROOT/'outputs/flash_converter_trial'
    for folder in sorted((base/'native_replacements').glob('native[0-9][0-9]')):
        proof=folder/'live_verification.json';payload=folder/'deployment_payload.json'
        if not(proof.is_file() and payload.is_file()):continue
        if json.loads(proof.read_text())['status']!='passed':continue
        for e in json.loads(payload.read_text())['entries']:add(e['bundle'],e['sha256'],proof)
    folders=[base/'FlyingMoglin',*[base/f'batch{i:02}' for i in range(2,7)],*[base/n/'conversion' for n in ('Caladbolg','OmegaRapier','CryoChronomancer')],ROOT/'outputs/battlepet_forge_lab/live']
    for folder in folders:
        proof=folder/'live_verification.json';payload=folder/'deployment_payload.json'
        if not(proof.is_file() and payload.is_file()):continue
        if json.loads(proof.read_text())['status']!='passed':continue
        p=json.loads(payload.read_text())
        if 'bundle' in p:add(p['bundle'],p.get('bundle_sha256'),proof)
        for e in p.get('entries',[]):add(e['bundle'],e.get('bundle_sha256') or e.get('sha256'),proof)
    for folder in (base/'skin_fix',base/'body_limb_fix'):
        proof=folder/'live_verification.json';deployment=folder/'deployment_result.json'
        if proof.is_file() and deployment.is_file() and json.loads(proof.read_text())['status']=='passed':
            for e in json.loads(deployment.read_text())['entries']:add(e['bundle'],e['sha256'],proof)
    return result


SCRIPT=r'''
import db,json,pathlib,hashlib,collections
c=db.connect();character=c.execute('SELECT id,account_id,name FROM characters WHERE id=8').fetchone()
assert character['id']==character['account_id']==8 and character['name'].lower()=='redux'
rows=c.execute('SELECT DISTINCT item_id FROM char_items WHERE char_id=8 ORDER BY item_id').fetchall()
root=pathlib.Path('/opt/infinity/content/game/assetbundles/windows');mirror=pathlib.Path('/var/www/swordshine/gamefiles')
checks={};items=[]
for owned in rows:
 iid=owned['item_id'];row=c.execute('SELECT * FROM items WHERE id=?',(iid,)).fetchone()
 if row is None:items.append(dict(item_id=iid,status='missing_catalog'));continue
 original=dict(row);item=db.item(c,iid);bundle=item.get('Bundle') or {};bid=int(bundle.get('ID') or 0)
 filename=bundle.get('Filename') or bundle.get('FileName') or '';version=max(int(bundle.get(k) or 0) for k in ('Version','VersionLive','VersionContent','VersionStage'))
 protected='caladbolg' in ((original['Name'] or '')+' '+(original['File'] or '')).lower()
 if bid not in checks:
  claim=CLAIMS.get(str(bid));valid=False
  if claim and filename and '..' not in pathlib.PurePosixPath(filename).parts:
   rel=pathlib.Path(filename);binary=root/rel.parent/rel.stem/str(version)/rel.name
   valid=version==claim['version'] and str(bundle.get('CRCPC',''))==claim['crc'] and binary.is_file() and hashlib.sha256(binary.read_bytes()).hexdigest()==claim['sha256']
  checks[bid]=dict(verified=valid,version=version,claim=claim)
 status='protected' if protected else ('converted' if checks[bid]['verified'] else 'unconverted')
 if status=='unconverted' and original['Type']=='Item' and not (original['File'] or '').lower().endswith('.swf') and not bid:
  status='no_visual_asset'
 entry=dict(item_id=iid,name=original['Name'],type=original['Type'],bundle_id=bid,version=version,status=status)
 if status=='unconverted':
  source=original['File'] or '';rel=pathlib.PurePosixPath(source)
  safe=bool(source) and not rel.is_absolute() and '..' not in rel.parts and '\\' not in source
  paths=['classes/'+g+'/'+pathlib.Path(source).name for g in ('M','F')] if original['Type'] in ('Armor','Class') and source.lower().endswith('.swf') else [source]
  sources=[]
  for relative in paths:
   f=mirror/relative
   sources.append(dict(relative=relative,exists=bool(safe and f.is_file()),sha256=hashlib.sha256(f.read_bytes()).hexdigest() if safe and f.is_file() else None))
  entry.update(original=original,template=item,sources=sources)
 items.append(entry)
print(json.dumps(dict(distinct_owned_items=len(rows),counts=dict(collections.Counter(e['status'] for e in items)),items=items),default=str));c.close()
'''


def main():
    OUT.mkdir(exist_ok=True);proofs=claims();(OUT/'verified_claims.json').write_text(json.dumps(proofs,indent=2))
    result=json.loads(server_script('-',data=('CLAIMS='+repr({str(k):v for k,v in proofs.items()})+'\n'+SCRIPT).encode()))
    (OUT/'audit.json').write_text(json.dumps(result,indent=2))
    missing=[r for r in result['items'] if r['status']=='unconverted']
    (OUT/'remaining.json').write_text(json.dumps(missing,indent=2))
    print(json.dumps(dict(distinct_owned_items=result['distinct_owned_items'],counts=result['counts'],remaining_types=dict(Counter(r['type'] for r in missing)),examples=[{k:r[k] for k in ('item_id','name','type','bundle_id')} for r in missing[:12]]),indent=2))


if __name__=='__main__':main()
