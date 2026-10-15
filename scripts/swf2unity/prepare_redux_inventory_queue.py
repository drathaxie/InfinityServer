"""Select public source owners for unconverted bundles actually owned by Redux."""
from collections import Counter
import json
from pathlib import Path
from trial_host import ROOT,server_script
from audit_redux_conversions import OUT
from specialty_batches import TYPES


def main():
    remaining=json.loads((OUT/'remaining.json').read_text())
    ids=sorted({r['bundle_id'] for r in remaining if 0<r['bundle_id']<2000000})
    script=r'''
import db,json,pathlib,hashlib
c=db.connect();rows=[];mirror=pathlib.Path('/var/www/swordshine/gamefiles')
for bid in IDS:
 owners=c.execute("SELECT * FROM items WHERE JSON_VALID(bundle) AND CAST(JSON_UNQUOTE(JSON_EXTRACT(bundle,'$.ID')) AS UNSIGNED)=? ORDER BY id",(bid,)).fetchall()
 for owner in owners:
  original=dict(owner);source=original['File'] or '';link=original['Link'] or ''
  if not source.lower().endswith('.swf') or not link or 'caladbolg' in (original['Name']+' '+source).lower():continue
  paths=['classes/'+g+'/'+pathlib.Path(source).name for g in ('M','F')] if original['Type'] in ('Armor','Class') else [source]
  if '..' in pathlib.PurePosixPath(source).parts or pathlib.PurePosixPath(source).is_absolute():continue
  files=[mirror/p for p in paths]
  if not all(f.is_file() for f in files):continue
  sources=[dict(relative=p,sha256=hashlib.sha256(f.read_bytes()).hexdigest(),bytes=f.stat().st_size) for p,f in zip(paths,files)]
  rows.append(dict(original=original,template=db.item(c,original['id']),sources=sources,source=sources[0]))
print(json.dumps(rows,default=str));c.close()
'''
    sources=json.loads(server_script('-',data=('IDS='+repr(ids)+'\n'+script).encode()))
    by_bundle={}
    for r in sources:
        bid=r['template']['Bundle']['ID']
        if bid not in by_bundle or any(o['item_id']==r['original']['id'] for o in remaining):by_bundle[bid]=r
    native=[dict(row,owned_item_ids=[o['item_id'] for o in remaining if o['bundle_id']==bid]) for bid,row in by_bundle.items()]
    specialty=[r for r in native if r['original']['Type'] in TYPES]
    armor=[r for r in native if r['original']['Type'] in ('Armor','Class')]
    nopath=[{k:r[k] for k in ('item_id','name','type','bundle_id')} for r in remaining if r['bundle_id'] not in by_bundle]
    for name,value in [('native_candidates.json',native),('specialty_candidates.json',specialty),('armor_candidates.json',armor),('remaining_source_work.json',nopath)]:
        (OUT/name).write_text(json.dumps(value,indent=2))
    (OUT/'selection.json').write_text(json.dumps(dict(mode='redux_inventory_only',specialty_file=str(OUT/'specialty_candidates.json'),armor_file=str(OUT/'armor_candidates.json')),indent=2))
    print(json.dumps(dict(native_source_bundles=len(native),specialty=len(specialty),armors=len(armor),types=dict(Counter(r['original']['Type'] for r in native)),remaining_source_work=len(nopath)),indent=2))


if __name__=='__main__':main()
