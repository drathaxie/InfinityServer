"""Inventory original catalog/source coverage without claiming conversion support."""
import json
from trial_host import server_script
import native_batches as n

SCRIPT = '''import db,json,pathlib,collections
c=db.connect();root=pathlib.Path('/var/www/swordshine/gamefiles')
rows=[];counts=collections.Counter();missing=collections.Counter();invalid=collections.Counter()
for row in c.execute('SELECT id,Name,Type,File,Link,bundle FROM items WHERE id<2000000 ORDER BY id DESC').fetchall():
 r=dict(row);counts[r['Type']]+=1
 if 'caladbolg' in ((r['Name'] or '')+' '+(r['File'] or '')).lower():continue
 try:b=json.loads(r['bundle']);bid=int(b.get('ID',0))
 except (ValueError,TypeError,AttributeError):invalid[r['Type']]+=1;continue
 if not 0<bid<2000000:invalid[r['Type']]+=1;continue
 source=pathlib.PurePosixPath(r['File'] or '')
 if source.is_absolute() or '..' in source.parts:continue
 variants=[str(source)]
 if r['Type'] in ('Armor','Class'):
  variants=['classes/M/'+source.name,'classes/F/'+source.name]
 present=[p for p in variants if p.lower().endswith('.swf') and (root/p).is_file()]
 if not present:missing[r['Type']]+=1;continue
 r.pop('bundle');r.update(bundle_id=bid,bundle_filename=b.get('FileName') or b.get('Filename'),
     sources_present=present,all_source_variants_present=len(present)==len(variants))
 rows.append(r)
print(json.dumps(dict(records=rows,all_record_counts=dict(counts),missing_flash_source_counts=dict(missing),invalid_bundle_counts=dict(invalid)),default=str));c.close()
'''


def main():
    from collections import Counter
    result = json.loads(server_script('-', data=SCRIPT.encode()))
    result['proof_boundary'] = 'Read-only original item/source inventory; source presence is not supported behavior, conversion, publication, or player proof.'
    result['source_record_counts'] = dict(Counter(r['Type'] for r in result['records']))
    result['unique_native_bundles_with_source'] = len({r['bundle_id'] for r in result['records']})
    n.write(n.BASE/'universe_inventory.json',result)
    print(json.dumps(dict(records=len(result['records']),bundles=result['unique_native_bundles_with_source'],
                         by_type=result['source_record_counts']),indent=2))


if __name__ == '__main__':
    main()
