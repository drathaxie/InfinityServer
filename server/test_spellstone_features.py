"""Configured transformations and synchronized room cheer over native client packets."""
import asyncio
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

import combat
import db
import forge
import seed
import spellstones
import world
from handlers.combat_cmds import begin_cast
from handlers.items import use_spellstone
from test_review_regressions import conn, player, Writer  # noqa: F401


def packets(writer):
    return [json.loads(part) for part in writer.data.split(b'\0') if part]


@pytest.fixture
def setup(conn, monkeypatch, tmp_path):
    pool = tmp_path / 'transformations.json'
    pool.write_text(json.dumps({'monsters': [
        {'name': 'First', 'linkage': 'FirstPrefab', 'bundle': {'ID': 1, 'Filename': 'npcs/first.unity3d'}},
        {'name': 'Second', 'linkage': 'SecondPrefab', 'scale': 0.75,
         'bundle': {'ID': 2, 'Filename': 'npcs/second.unity3d'}}]}))
    monkeypatch.setattr(spellstones, 'POOL_FILE', pool)
    monkeypatch.setattr(world, '_rooms', {})
    monkeypatch.setattr(spellstones, '_room_cheers', {})
    seed.seed_practice_spellstone(conn)
    seed.seed_cheer_spellstone(conn)
    conn.commit()
    peers = []
    for i in range(4):
        char = player(conn, 'spellstone_peer_' + str(i))
        writer = Writer()
        writer.data = b''
        # The existing writer records chunks in a list; use a byte-buffer writer here.
        writer.write = lambda data, w=writer: setattr(w, 'data', w.data + data)
        member = world.Member(char['id'], char['name'], {}, writer)
        world.join(member, 'spellstone-room' if i < 3 else 'another-instance')
        member.frame = 'Enter' if i != 2 else 'OtherCell'
        combat.forget_player(member.uid)
        combat.register_player(member.uid, 100)
        peers.append(SimpleNamespace(conn=conn, char=char, member=member,
                                     area=member.area, equipped_class=1932, writer=writer))
    yield peers, pool
    for peer in peers:
        combat.forget_player(peer.member.uid)
    combat._mon.pop(('spellstone-room', 'm:9888'), None)
    combat._moninfo.pop(('spellstone-room', 'm:9888'), None)
    combat._aggro.pop(('spellstone-room', 'm:9888'), None)


def equip(session, item_id):
    current = session.conn.execute('SELECT COALESCE(MAX(char_item_id),0)+1 FROM char_items').fetchone()[0]
    session.conn.execute('INSERT INTO char_items(char_item_id,char_id,item_id,quantity) VALUES(?,?,?,1)',
                         (current, session.char['id'], item_id))
    session.conn.commit()
    asyncio.run(use_spellstone(session, session.writer, 'useSpellstone', [str(item_id)], {}))
    session.writer.data = b''


def cast(session, target=''):
    asyncio.run(begin_cast(session, session.writer, 'gar', ['5', target], {}))


def test_random_transform_is_selected_once_and_shared_with_other_clients(conn, setup, monkeypatch):
    peers, _ = setup
    caster, other = peers[:2]
    equip(caster, spellstones.TRANSFORMATION_ITEM_ID)
    seen = []
    def choose(pool):
        result = pool[len(seen) % len(pool)]
        seen.append(result['Linkage'])
        return result
    monkeypatch.setattr(spellstones.random, 'choice', choose)
    original = deepcopy(forge.equipped_spellstone(conn, caster.char['id'])['data'])
    for expected in ('FirstPrefab', 'SecondPrefab'):
        caster.writer.data = other.writer.data = b''
        combat.release_cooldown(caster.member.uid, 5)
        combat._rp[caster.member.uid] = 17
        cast(caster, 'm:9999')  # stale selected monster must not acquire aggro
        attack = next(p for p in packets(caster.writer) if p['Cmd'] == 'Attack')
        node = next(n for n in attack['Nodes'] if n['Name'] == 'MonTransform')
        assert node['Linkage'] == expected
        assert attack in packets(other.writer)
        assert combat.resource_total(caster.member.uid) == 17
        assert not combat.in_combat(caster.member.uid, caster.area)
        assert (caster.area, 'm:9999') not in combat._aggro
    assert seen == ['FirstPrefab', 'SecondPrefab']
    assert forge.equipped_spellstone(conn, caster.char['id'])['data'] == original
    aura = combat._auras[(caster.area, f'p:{caster.member.uid}')]['Monster Transformation']
    aura['ends'] = 0
    expiry = combat.aura_ticks()
    assert any(any(n.get('detransform') for n in attack.get('Nodes', [])) for _, attack, _ in expiry)


@pytest.mark.parametrize('item_id', [spellstones.TRANSFORMATION_ITEM_ID, spellstones.CHEER_ITEM_ID])
def test_stones_cannot_cast_in_combat_or_while_dead(conn, setup, item_id):
    peers, _ = setup
    caster = peers[0]
    equip(caster, item_id)
    combat._mon[(caster.area, 'm:9888')] = 100
    combat.engage(caster.area, 'm:9888', caster.member.uid)
    cast(caster)
    assert packets(caster.writer)[-1]['StatusCode'] == 0
    assert (caster.member.uid, 5) not in combat._cast_last
    combat.cancel_player_actions(caster.member.uid)
    combat._php[caster.member.uid] = 0
    caster.writer.data = b''
    cast(caster)
    assert packets(caster.writer)[-1]['StatusCode'] == 0
    assert (caster.member.uid, 5) not in combat._cast_last


def test_invalid_pool_fails_before_cooldown_and_catalog_ids_resolve(conn, setup):
    peers, path = setup
    caster = peers[0]
    equip(caster, spellstones.TRANSFORMATION_ITEM_ID)
    for raw in ('{broken', '{"monsters":[]}', '{"monsters":[{"monster_id":99999}]}'):
        path.write_text(raw)
        caster.writer.data = b''
        cast(caster)
        assert packets(caster.writer)[-1]['StatusCode'] == 0
        assert (caster.member.uid, 5) not in combat._cast_last
    conn.execute('INSERT INTO monsters(mon_id,name,linkage,bundle,scale) VALUES(991,?,?,?,?)',
                 ('Catalog monster', 'CatalogPrefab', json.dumps({'ID': 99, 'Filename': 'npcs/catalog.unity3d'}), 1.25))
    conn.commit()
    path.write_text('{"monsters":[{"monster_id":991,"scale":0.8}]}')
    assert spellstones.transformation_pool(conn)[0]['Scale'] == 0.8
    caster.writer.data = b''
    cast(caster)
    assert any(n.get('Linkage') == 'CatalogPrefab' for p in packets(caster.writer) for n in p.get('Nodes', []))


def test_cheer_is_scoped_and_room_cooldown_survives_another_caster(conn, setup):
    peers, _ = setup
    caster, other, different_cell, different_instance = peers
    equip(caster, spellstones.CHEER_ITEM_ID)
    equip(other, spellstones.CHEER_ITEM_ID)
    cast(caster)
    for viewer in (caster, other):
        cheers = [p for p in packets(viewer.writer) if p['Cmd'] == 'chatm']
        assert {p['ID'] for p in cheers} == {caster.member.uid, other.member.uid}
        assert all(p['msg'] == 'Battle on!' and p['channel'] == 'zone' for p in cheers)
        emotes = [p for p in packets(viewer.writer) if p['Cmd'] == 'emotea']
        assert {p['userID'] for p in emotes} == {caster.member.uid, other.member.uid}
        assert len(emotes) == len(cheers) == 2
        assert all(p['strEmote'] == 'Em_Cheer' for p in emotes)
    for viewer in (different_cell, different_instance):
        assert not any(p['Cmd'] == 'chatm' for p in packets(viewer.writer))
        assert not any(p['Cmd'] == 'emotea' for p in packets(viewer.writer))
    other.writer.data = b''
    cast(other)
    assert packets(other.writer)[-1]['StatusCode'] == 0
    assert (other.member.uid, 5) not in combat._cast_last
    spellstones._room_cheers[(caster.area, caster.member.frame)] = 0
    other.writer.data = b''
    cast(other)
    assert any(p['Cmd'] == 'chatm' for p in packets(other.writer))


def test_cheer_skips_fighting_players_and_unequipped_items_cannot_cast(conn, setup):
    peers, _ = setup
    caster, other = peers[:2]
    cast(caster)
    assert packets(caster.writer)[-1]['StatusCode'] == 0
    equip(caster, spellstones.CHEER_ITEM_ID)
    combat._mon[(caster.area, 'm:9888')] = 100
    combat.engage(caster.area, 'm:9888', other.member.uid)
    caster.writer.data = b''
    cast(caster)
    cheers = [p for p in packets(caster.writer) if p['Cmd'] == 'chatm']
    assert [p['ID'] for p in cheers] == [caster.member.uid]
    assert [p['userID'] for p in packets(caster.writer) if p['Cmd'] == 'emotea'] == [caster.member.uid]
    assert combat.in_combat(other.member.uid, caster.area)
