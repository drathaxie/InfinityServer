"""Complete gem families, native purchase delivery, preview integrity and shared IDs."""
import asyncio
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import db
import game
import gem_balance
import loot
import patterns
from handlers.economy import buy_sell
from test_review_regressions import conn, player, simultaneous  # noqa: F401

ROOT = Path(__file__).resolve().parent.parent


def catalog():
    return (json.loads((ROOT/'data/items.json').read_text(encoding='utf-8')),
            json.loads((ROOT/'data/gem_catalog.json').read_text(encoding='utf-8'))['entries'])


def test_every_supported_family_has_four_slots_and_six_rollable_rarities():
    items, entries = catalog()
    assert len(entries) == len(patterns._ARCHETYPE_PRIMARY) * len(patterns.ENHANCEABLE)
    assert {(e['family'], e['equip_spot']) for e in entries} == {
        (family, slot) for family in patterns._ARCHETYPE_PRIMARY for slot in patterns.ENHANCEABLE}
    assert len({e['item_id'] for e in entries}) == len(entries)
    for entry in entries:
        item = items[str(entry['item_id'])]
        assert item['ItemType'] == 43 and item['EquipSpot'] == 1
        assert patterns.archetype_of(item['Name']) == entry['family']
        original = deepcopy(item)
        for quality in patterns.QUALITY_NAME:
            p = patterns.gem_item_pattern(item, quality=quality)
            assert p['EquipSpot'] == entry['equip_spot'] and p['Quality'] == quality
            primary = entry['primary_stat']
            low, high = gem_balance.primary_bounds(p['Level'], p['EquipSpot'], quality, entry['family'])
            assert low <= p[primary] <= high
            assert p['Power'] == sum(p[s] for s in gem_balance.STATS)
            assert all(p[primary] > p[s] for s in ('STR','DEX','INT','WIS','END','LUK') if s != primary)
            assert p['Dust'] > 0
        assert item == original


def test_catalog_completion_preserves_existing_definitions_and_is_idempotent():
    spec = importlib.util.spec_from_file_location('complete_gem_catalog', ROOT/'scripts/complete_gem_catalog.py')
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    items, entries = catalog()
    base = {k:v for k,v in items.items() if not 210000 <= int(k) < 210052}
    result, manifest, added = module.build(base)
    assert len(added) == 47
    assert all(result[k] == v for k,v in base.items())
    repeated, again, second = module.build(result)
    assert second == [] and repeated == result and again == manifest
    assert manifest['entries'] == entries


@pytest.mark.parametrize('coins', [False, True])
def test_bought_gem_enters_pattern_bag_with_live_currency_and_can_equip_and_dust(conn, coins):
    items, _ = catalog()
    item = items['210000']  # Warrior Weapon
    db.store_item(conn, {**item, 'Cost': 3, 'Coins': coins})
    char = player(conn, 'gem_buyer')
    conn.execute('INSERT INTO shop_items(shop_id,shop_item_id,item_id,cost,coins,quantity_remain) VALUES(12345,1,?,3,?,1)', (item['ID'], int(coins)))
    conn.commit()
    class Writer:
        def __init__(self): self.packets=[]
        def write(self, b): self.packets.append(json.loads(b.rstrip(b'\0')))
        async def drain(self): pass
    writer = Writer()
    session = SimpleNamespace(conn=conn, char=char)
    asyncio.run(buy_sell(session, writer, 'buyItem', ['0','12345','1'], {}))
    reply, add, currency = writer.packets
    assert reply['Success'] and 'item' not in reply and '_patternItem' not in reply
    assert add['Cmd']=='addItems' and add['items']==[] and len(add['patternItems'])==1
    gem = add['patternItems'][0]
    assert patterns.loose_gems(conn, char['id'])[0]['pattern'] == gem['pattern']
    assert conn.execute('SELECT COUNT(*) FROM char_items WHERE char_id=? AND item_id=?',(char['id'],item['ID'])).fetchone()[0]==0
    if coins:
        assert currency['Cmd']=='upgradeSync' and currency['Coins']==7
    else:
        assert currency['Cmd']=='addGoldXP' and currency['Gold']['val']==-3 and currency['ExpTotal']==char['exp']
    assert not game.buy(conn, char, ['0','12345','1'])['Success']  # finite stock cannot duplicate
    db.store_item(conn, {'ID':888001,'Name':'Test sword','EquipSpot':2,'ItemType':1})
    game._grant_item(conn, char['id'], db.item(conn,888001)); conn.commit()
    equipped = patterns.equip_pattern(conn,char['id'],888001,gem['CharPatternID'])
    assert equipped['Success'] and equipped['pattern']==gem['pattern']
    replacement = patterns.grant_gem(conn,char['id'],patterns.gem_item_pattern(item)); conn.commit()
    swapped = patterns.equip_pattern(conn,char['id'],888001,replacement)
    old = swapped['_bouncedPatternItem']
    assert old['pattern']==gem['pattern']
    result = patterns.dust_pattern(conn,char['id'],old['CharPatternID'])
    assert result['bSuccess'] and result['Gained']==gem['pattern']['Dust']
    assert not patterns.dust_pattern(conn,char['id'],old['CharPatternID'])['bSuccess']


def test_gem_preview_claim_and_pending_dust_use_one_roll(conn, monkeypatch):
    items,_ = catalog(); item = items['210000']
    char = player(conn,'gem_preview'); uid=123456
    monkeypatch.setattr(loot,'_pending',{})
    shown = loot.add_pending(uid,[item])[0]
    expected = deepcopy(shown['ItemPattern'])
    monkeypatch.setattr(patterns,'gem_item_pattern',lambda *a,**k: pytest.fail('Preview was rerolled'))
    add,reply = loot.take(conn,char['id'],uid,item['ID'],shown['LootID'])
    assert reply['bSuccess'] and add['patternItems'][0]['pattern']==expected
    shown2 = loot.add_pending(uid,[{**item,'ItemPattern':expected}])[0]
    pat,iid=loot.dust_pending_pattern(uid,shown2['LootID'])
    assert pat==expected and iid==item['ID']


def test_gems_and_gear_share_atomic_instance_allocation(conn):
    char=player(conn,'gem_ids')
    gem=patterns.roll_pattern({'EquipSpot':2},archetype='lucky')
    db.store_item(conn,{'ID':888002,'Name':'Gear','ItemType':1,'EquipSpot':3});conn.commit()
    def grant(c, make_gem):
        result=(patterns.grant_gem(c,char['id'],gem) if make_gem else
                game._grant_item(c,char['id'],{**db.item(c,888002),'ItemPattern':gem}))
        c.commit();return result
    ids=simultaneous([lambda c,i=i: grant(c,i%2==0) for i in range(8)])
    assert len(set(ids))==8
