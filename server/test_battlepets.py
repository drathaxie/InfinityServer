"""Contracts for the opt-in Forge companion prototype, without enabling live pets."""
from unittest.mock import patch

import pytest

import battlepets
import combat
import json
import world
from combat_engine.engine import RenderContext
from combat_engine.nodes import render_node


@pytest.mark.parametrize('state', battlepets.STATES)
def test_pet_animation_renderers_agree(state):
    props = {'Name': 'PetAnimation', 'Animation': state, 'Duration': 1.25, 'Speed': 2}
    ctx = RenderContext('p:930001', slot=2, target='m:1')
    legacy, damage, killed = combat._render_node('pet-contract', 2, ctx.caster, ['m:1'], {}, props)
    assert legacy == render_node(ctx, props) == {
        'Name': 'PetAnimation', 'Animation': state, 'Duration': 1.25, 'Speed': 2., 'Target': 'm:1'}
    assert damage == 0 and killed == []


@pytest.mark.parametrize('delta', [
    {'Animation': 'Idle'}, {'Animation': 'Attack1,Attack2'}, {'Duration': float('nan')},
    {'Duration': float('inf')}, {'Duration': .01}, {'Duration': 11}, {'Speed': 0},
    {'Speed': float('nan')}, {'Speed': 5}])
def test_invalid_commands_rejected(delta):
    with pytest.raises(ValueError):
        battlepets.render_animation({'Animation': 'Attack1', 'Duration': 1, 'Speed': 1} | delta)


@pytest.mark.parametrize('delta', [
    {'owner_alive': False}, {'equipped_pet': None}, {'equipped_pet': 2},
    {'supported_states': {'Idle','Walk'}}, {'same_instance': False}, {'same_frame': False},
    {'target_alive': False}, {'distance': float('nan')}, {'distance': -1}, {'distance': 9}])
def test_admission_does_not_trust_wrong_equipment_or_room(delta):
    inputs = dict(owner_alive=True, equipped_pet=6673, required_pet=6673,
                  supported_states={'Attack1'}, state='Attack1', same_instance=True,
                  same_frame=True, target_alive=True, distance=3.)
    assert battlepets.admit(**inputs) is None
    assert battlepets.admit(**(inputs | delta)) is not None


@pytest.mark.parametrize('immediate', [None, False, True])
@pytest.mark.parametrize('heal', [False, True])
def test_immediate_is_preserved_only_when_authored(immediate, heal):
    area, uid = 'pet-damage-contract', 930001
    combat.register_player(uid, 1000)
    combat._php[uid] = 500
    combat.register_monster(area, 'm:1', 1000)
    props = {'Name': 'Damage', 'Heal': heal, 'Multiplier': 1}
    if immediate is not None:
        props['Immediate'] = immediate
    with patch.object(combat, '_hit', return_value=(20, 0)), \
         patch.object(combat, '_heal_amount', return_value=(20, False)):
        node, _, _ = combat._render_node(area, 1, f'p:{uid}', ['m:1'], {}, props)
    assert ('Immediate' in node) == (immediate is not None)
    if immediate is not None:
        assert node['Immediate'] is immediate


def test_old_clients_never_receive_custom_nodes():
    class Writer:
        def __init__(self): self.data=b''
        def write(self,data): self.data+=data
    old_writer,new_writer=Writer(),Writer()
    old=world.Member(930011,'old',{},old_writer);new=world.Member(930012,'new',{},new_writer)
    new.battlepet_v1=True
    area='battlepet-wire-contract';world._rooms[area]={old.uid:old,new.uid:new}
    packet={'Cmd':'Attack','Nodes':[{'Name':'PetAnimation','Animation':'Attack1'},{'Name':'Damage','Immediate':True}]}
    try:
        world.broadcast(area,packet)
        before=json.loads(old_writer.data.rstrip(b'\0'));after=json.loads(new_writer.data.rstrip(b'\0'))
        assert [n['Name'] for n in before['Nodes']]==['Damage']
        assert [n['Name'] for n in after['Nodes']]==['PetAnimation','Damage']
        assert len(packet['Nodes'])==2
    finally:world._rooms.pop(area,None)


def test_live_admission_requires_current_equipment(monkeypatch,tmp_path):
    import game
    from types import SimpleNamespace
    from pathlib import Path
    uid=930021;area='pet-equipment-contract';target='m:21'
    combat.register_player(uid,1000);combat.register_monster(area,target,1000,frame='Enter',x=1,y=1)
    member=SimpleNamespace(uid=uid,frame='Enter',x=0.,y=0.,battlepet_v1=True)
    class Conn:
        def execute(self,*args):return self
        def fetchone(self):return {'raw':json.dumps({'battlepet':{'pet_item_id':6673}})}
    session=SimpleNamespace(member=member,char={'id':8},conn=Conn(),equipped_class=1,area=area)
    source=Path(battlepets.__file__).resolve().parent.parent/'data/battlepet_capabilities.json'
    real_read=Path.read_text;real_isfile=Path.is_file
    cap={'6673':{'bundle_id':99,'prefab':'Pet','states':{'Attack1':1.},'max_range':8}}
    monkeypatch.setattr(Path,'is_file',lambda p:True if p==source else real_isfile(p))
    monkeypatch.setattr(Path,'read_text',lambda p,*a,**kw:json.dumps(cap) if p==source else real_read(p,*a,**kw))
    equipped={'Pet':{'ID':6673,'Bundle':{'ID':99},'PrefabName':'Pet'}}
    monkeypatch.setattr(game,'_equipped_rig',lambda *a:equipped)
    order=[('1',{'Name':'PetAnimation','Animation':'Attack1','Duration':1})]
    assert battlepets.check_skill(session,{},target,order=order) is None
    equipped.clear()
    assert 'Equip' in battlepets.check_skill(session,{},target,order=order)
    member.battlepet_v1=False
    assert 'Restart' in battlepets.check_skill(session,{},target,order=order)
