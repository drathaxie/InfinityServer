"""Budget conservation, progression guarantees, acquisition levels and persistence boundaries."""
import json
import importlib.util
from pathlib import Path
import random

import pytest
import db
import game
import gem_balance as balance
import loot
import patterns
from test_review_regressions import conn, player  # noqa: F401


def test_all_family_slot_tier_level_extremes_conserve_budget_and_improve_primary():
    for level in range(1, 101):
        for slot in balance.SLOT_SHARE:
            for family, (primary, _, _) in balance.PROFILES.items():
                previous = None
                for quality in range(5,11):
                    lo,hi = balance.primary_bounds(level,slot,quality,family)
                    if previous is not None:
                        assert lo > previous
                    previous = hi
                    for seed in (0,17):
                        p=balance.roll(level,slot,quality,family,random.Random(seed))
                        assert p['Power']==sum(p[s] for s in balance.STATS)
                        assert lo<=p[primary]<=hi
                        assert all(0<=p[s]<p[primary] for s in balance.STATS if s!=primary)
                        assert p['BalanceVersion']==2


def test_slot_level_and_weapon_growth_are_bounded():
    for level in range(1,101):
        for quality in range(5,11):
            budgets=[balance.budget(level,s,quality) for s in (7,2,3,4)]
            assert budgets==sorted(budgets,reverse=True)
            for slot in balance.SLOT_SHARE:
                if level>1:
                    assert balance.budget(level,slot,quality)>balance.budget(level-1,slot,quality)
    for level in (1,5,6,25,50,100):
        common=balance.roll(level,2,5,'warrior',random.Random(0))
        mythic=balance.roll(level,2,10,'warrior',random.Random(0))
        assert 1.35 <= mythic['Base']/common['Base'] <= 1.46
        head=balance.roll(level,3,5,'warrior')
        assert head['Base']==head['HP'] and head['Wild']==0
        for slot in (4,7):
            gear=balance.roll(level,slot,5,'warrior')
            assert gear['Base']==gear['HP']==gear['Wild']==0
    assert balance.roll(5,2,5,'warrior')['Base']==31


def test_drop_level_is_capped_by_encounter_and_player_and_legacy_tokens_roll_v2(conn, monkeypatch):
    item={'ID':777001,'Name':'Wizard Armor Gem','ItemType':43,'EquipSpot':1,'Level':1,
          'ItemPattern':{'Name':'old static example','Level':5,'INT':999,'EquipSpot':7}}
    db.store_item(conn,item)
    conn.execute('INSERT INTO monsters(mon_id,level) VALUES(777001,3)')
    conn.execute('INSERT INTO monster_drops(mon_id,item_id,rate,quantity) VALUES(777001,777001,1,1)')
    conn.commit()
    for player_level,expected in ((1,1),(6,3)):
        drop=loot.roll_drops(conn,777001,player_level=player_level)[0]
        assert drop['ItemPattern']['Level']==expected and drop['ItemPattern']['BalanceVersion']==2
        assert drop['ItemPattern']['INT']<999
        monkeypatch.setattr(loot,'_pending',{})
        shown=loot.add_pending(456,[drop])[0]
        assert shown['ItemPattern']==drop['ItemPattern']


def test_shop_uses_owner_level_and_overlevel_apply_is_rejected_without_consuming(conn):
    char=player(conn,'balance_owner')
    conn.execute('UPDATE characters SET level=4 WHERE id=?',(char['id'],))
    db.store_item(conn,{'ID':777002,'Name':'Lucky Weapon Gem','ItemType':43,'EquipSpot':1,'Level':1,
                        'ItemPattern':{'Quality':5,'Level':5,'LUK':999,'EquipSpot':2}})
    conn.execute('INSERT INTO shop_items(shop_id,shop_item_id,item_id,cost,coins,quantity_remain) VALUES(777002,1,777002,0,0,-1)')
    conn.commit()
    result=game.buy(conn,char,[0,777002,1])
    gem=result['_patternItem']['pattern']
    assert gem['Level']==4 and gem['BalanceVersion']==2
    db.store_item(conn,{'ID':777003,'Name':'Weapon','EquipSpot':2})
    game._grant_item(conn,char['id'],db.item(conn,777003))
    too_high=patterns.grant_gem(conn,char['id'],balance.roll(5,2,7,'lucky'))
    conn.commit()
    assert not patterns.equip_pattern(conn,char['id'],777003,too_high)['Success']
    assert any(g['CharPatternID']==too_high for g in patterns.loose_gems(conn,char['id']))


def test_owned_legacy_patterns_are_not_rerolled_by_reads_or_dusting(conn):
    char=player(conn,'legacy_owner')
    old={'ID':1,'Name':'Rare Fighter','Quality':7,'Level':5,'EquipSpot':2,'Base':95,'Wild':0.1,
         'STR':21,'DEX':3,'END':2,'INT':0,'WIS':1,'LUK':2,'Power':40}
    cpid=patterns.grant_gem(conn,char['id'],old);conn.commit()
    assert patterns.loose_gems(conn,char['id'])[0]['pattern']==old
    assert json.loads(conn.execute('SELECT pattern_json FROM char_patterns WHERE char_pattern_id=?',(cpid,)).fetchone()[0])==old
    assert patterns.dust_pattern(conn,char['id'],cpid)['Gained']==20


def test_free_powerup_scales_preserves_applied_gems_and_cannot_farm_dust(conn):
    char=player(conn,'powerup_owner')
    db.store_item(conn,{'ID':777004,'Name':'Weapon','EquipSpot':2,'Level':50})
    assert not patterns.item_default_pattern(conn,char['id'],777004)['Success']
    game._grant_item(conn,char['id'],db.item(conn,777004));conn.commit()
    first=patterns.item_default_pattern(conn,char['id'],777004)
    assert first['Success'] and first['pattern']['Level']==1
    assert first['pattern']['Power']==sum(first['pattern'][s] for s in balance.STATS)
    assert patterns.dust_value(first['pattern'])==0
    assert patterns.item_default_pattern(conn,char['id'],777004)['pattern']==first['pattern']
    cpid=patterns.grant_gem(conn,char['id'],balance.roll(1,2,10,'warrior'));conn.commit()
    applied=patterns.equip_pattern(conn,char['id'],777004,cpid)
    assert applied['Success']
    bounced=applied['_bouncedPatternItem']
    assert patterns.dust_pattern(conn,char['id'],bounced['CharPatternID'])['Gained']==0
    assert patterns.item_default_pattern(conn,char['id'],777004)['pattern']==applied['pattern']


def test_legacy_migration_plan_is_deterministic_read_only_and_flags_unknown_families(conn):
    source=Path(__file__).resolve().parent.parent/'scripts/plan_gem_rebalance.py'
    spec=importlib.util.spec_from_file_location('plan_gem_rebalance',source)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    char=player(conn,'plan_owner')
    old={'Name':'Mythic Warrior','Level':5,'Quality':10,'EquipSpot':2,'STR':30,'Base':230,'Power':80}
    known=patterns.grant_gem(conn,char['id'],old)
    unknown=patterns.grant_gem(conn,char['id'],{**old,'Name':'Unrecognized family'})
    current=patterns.grant_gem(conn,char['id'],balance.roll(1,2,10,'warrior'))
    conn.commit()
    before=patterns.loose_gems(conn,char['id'])
    plan=module.plan(conn)
    assert plan==module.plan(conn)
    assert [row['id'] for row in plan['changes']]==[known]
    assert [row['id'] for row in plan['manual_review']]==[unknown]
    assert current not in [row['id'] for row in plan['changes']]
    after=json.loads(plan['changes'][0]['after'])
    assert after['Level']==1 and after['Quality']==10 and after['Family']=='warrior'
    assert patterns.loose_gems(conn,char['id'])==before
