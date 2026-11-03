"""Read-only, deterministic migration plan for owned pre-v2 gems. Never changes the DB.

Use --output PATH to save a reviewable plan before any maintenance migration. The plan
includes exact before/after JSON and owner IDs; keep it in a private backup directory.
Unknown patterns are listed for manual review rather than guessed or destroyed.
"""
import argparse
import hashlib
import json
from pathlib import Path
import random
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'server'))
import db
import gem_balance as balance
import patterns


def plan(conn):
    changes,review=[],[]
    for table,key in (('char_patterns','char_pattern_id'),('char_items','char_item_id')):
        rows=conn.execute(f'SELECT t.{key} AS instance_id,t.char_id,t.pattern_json,c.level AS owner_level '
                          f'FROM {table} t JOIN characters c ON c.id=t.char_id WHERE t.pattern_json IS NOT NULL')
        for row in rows:
            identity={'table':table,'key':key,'id':row['instance_id'],'char_id':row['char_id']}
            try:
                old=json.loads(row['pattern_json'])
                if not isinstance(old,dict):raise ValueError('not a Pattern object')
                if old.get('BalanceVersion')==balance.VERSION:continue
                slot=int(old.get('EquipSpot',0))
                if slot not in balance.SLOT_SHARE:raise ValueError('unsupported slot')
                name=str(old.get('Name',''))
                family=patterns.archetype_of(name)
                level=min(balance.normalize_level(old.get('Level',1)),balance.normalize_level(row['owner_level']))
                quality=int(old.get('Quality',5))
                if family is None and name in ('Common Weapon','Standard'):
                    new=balance.default(level,slot)
                elif family and quality in balance.QUALITY_NAME:
                    seed=hashlib.sha256((table+str(row['instance_id'])+row['pattern_json']).encode()).hexdigest()
                    new=balance.roll(level,slot,quality,family,random.Random(seed))
                else:raise ValueError('unrecognized family or rarity')
                changes.append({**identity,'before':row['pattern_json'],'after':json.dumps(new,separators=(',',':')),
                                'old_base':old.get('Base',0),'new_base':new['Base'],
                                'old_power':old.get('Power',0),'new_power':new['Power']})
            except (TypeError,ValueError) as exc:
                review.append({**identity,'reason':str(exc)})
    return {'balance_version':balance.VERSION,'status':'review_required_no_mutations',
            'changes':changes,'manual_review':review}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    with db.connect() as conn:
        if db.BACKEND=='postgres':conn.execute('SET TRANSACTION READ ONLY')
        result=plan(conn)
    if args.output:
        with args.output.open('x',encoding='utf-8') as f:json.dump(result,f,indent=2)
    print(json.dumps({'planned_changes':len(result['changes']),'manual_review':len(result['manual_review']),
                      'database_changed':False,'output':str(args.output) if args.output else None}))


if __name__=='__main__':main()
