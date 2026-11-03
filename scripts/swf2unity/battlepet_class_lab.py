"""Build/reopen/test a five-slot Forge battlepet class in isolated SQLite.

This is a class prototype, not a live import tool. It exports a proposed palette
and class payload without changing the server palette, any account or inventory.
"""
from contextlib import closing
import copy
import json
import math
import os
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
os.environ['INFINITY_DB'] = 'sqlite'
sys.path.insert(0, str(ROOT / 'server'))
import battlepets
import combat
import db
import forge
from combat_engine.engine import RenderContext
from combat_engine.nodes import render_node

LAB = ROOT / 'outputs/battlepet_forge_lab'
CLASS_ID = 9906673  # Isolated fixture ID, not a proposed live catalog allocation.
PET_ID = 6673


def graph(state, duration, cd, multiplier, *, heal=False):
    nodes = [('0', {'Name': 'OnRequest', 'Slot': -1}),
             ('1', {'Name': 'PetAnimation', 'Animation': state, 'Duration': duration, 'Speed': 1}),
             ('2', {'Name': 'Damage', 'Multiplier': multiplier, 'DamageType': 'Magical',
                    'Immediate': True, 'Heal': heal, 'Targets': {'id': '9'}}),
             ('3', {'Name': 'Cooldown', 'CD': cd})]
    data, layout = forge.linear_graph(nodes)
    data[1]['9'] = {'Name': 'Self' if heal else 'Target'}
    layout[0]['9'] = {'X': 0., 'Y': 220.}
    layout[1]['0']['Next']['Next']['Targets'] = {'id': '9'}
    return data, layout


SKILLS = [
    ('Pet Strike', 'Attack1', 29/24, 2000, .5, False, 0),
    ('Focused Strike', 'Attack1', 29/24, 4000, 1., False, 1),
    ('Daimyo Beam', 'Attack2', 18/24, 6500, 1.5, False, 1),
    ('Companion Mend', 'Buff', 23/24, 10000, 1., True, 1),
    ('Finishing Beam', 'Attack2', 18/24, 15000, 2.5, False, 1),
]


def main():
    LAB.mkdir(parents=True, exist_ok=True)
    checks, packets = [], []

    def check(name, value):
        if not value:
            raise AssertionError(name)
        checks.append(name)

    survey = json.loads((LAB/'source_survey.json').read_text(encoding='utf-8'))
    source = next(r for r in survey['inspected'] if r['item_id'] == PET_ID)
    # Root callback timing, not just the distance between labels, sets duration.
    check('Daimyo source frame rate matches class windows', source['fps'] == 24)
    states = next(t['labels'] for t in source['labeled_timelines'] if t['linkage'] == 'ArmoredDaimyoBattle')
    for _, state, *_ in SKILLS:
        check('Source contains ' + state, state in states)
    compiled = json.loads((LAB/'state_programs.json').read_text(encoding='utf-8'))
    state_contract = next(e for e in compiled['entries'] if e['item_id'] == PET_ID)
    check('Root program matches inspected source', state_contract['source_sha256'] == source['sha256'])
    for name, state, duration, *_ in SKILLS:
        behavior = state_contract['command_behavior'][state]
        check(name + ' duration matches callback clock', not behavior['immediate_redirect'] and
              abs(duration - behavior['ticks_to_idle']/source['fps']) < 1e-9)

    with tempfile.TemporaryDirectory(prefix='battlepet-class-', dir=LAB) as scratch:
        db.DB_PATH = Path(scratch)/'class.sqlite'
        with closing(db.connect()) as conn:
            with conn:
                db.initialize_connection(conn)
                conn.execute('INSERT INTO classes(class_id,name,resource,raw) VALUES(?,?,?,?)',
                    (CLASS_ID, 'Daimyo Commander Lab', json.dumps({'model': 'mana', 'MaxRP': 100}),
                     json.dumps({'battlepet_lab': True, 'required_pet': PET_ID})))
            for slot, (name, state, duration, cd, mult, heal, action) in enumerate(SKILLS):
                data, layout = graph(state, duration, cd, mult, heal=heal)
                reply = forge.sf_new(conn, [CLASS_ID, action, name,
                    'Experimental battlepet command; loader bridge and validated pet required.',
                    'iipet', slot, json.dumps(data), json.dumps(layout)])
                check(name + ' persisted through sfNew', reply['Cmd'] == 'sfNew')
        with closing(db.connect()) as conn:
            reopened = forge.build_init(conn)
            reopened['nodes'] = copy.deepcopy(reopened['nodes'])
            reopened['nodes']['PetAnimation'] = battlepets.PALETTE
            check('Five slots reopen in Forge', len(reopened['classes']['Daimyo Commander Lab']['Skills']) == 5)
            check('Existing Summon palette untouched', 'PetAnimation' not in forge.load_palette()['nodes'])
            skills = [forge.skill_for_slot(conn, CLASS_ID, slot) for slot in range(5)]
            seact = forge.build_seact(conn, CLASS_ID)
            check('All five skills reach native HUD wire payload', len(seact['skillList']) == 5)

        area, uid, target = 'battlepet-class-lab', 920001, 'm:1'
        combat.register_player(uid, 1000)
        combat.set_resource_model(uid, 'mana', 100)
        combat.register_monster(area, target, 10000, frame='Enter')
        admission = dict(owner_alive=True, equipped_pet=PET_ID, required_pet=PET_ID,
                         supported_states=set(states), state='Attack1', same_instance=True,
                         same_frame=True, target_alive=True, distance=3.)
        check('Validated fixture admission succeeds', battlepets.admit(**admission) is None)
        rejected = [dict(owner_alive=False), dict(equipped_pet=None), dict(equipped_pet=8053),
                    dict(supported_states={'Idle','Walk'}), dict(same_instance=False),
                    dict(same_frame=False), dict(target_alive=False), dict(distance=9.),
                    dict(distance=float('nan')), dict(distance=-1.)]
        for index, delta in enumerate(rejected):
            check('Admission rejects boundary ' + str(index), battlepets.admit(**(admission | delta)) is not None)

        # The native damage queues are tied to PLAYER animation hit cues. Pet
        # commands therefore use authored Immediate Damage for this first pilot.
        # This proves dispatch/HP, not impact synchronization or live admission.
        with patch.object(combat, '_hit', return_value=(20, 0)), patch.object(combat, '_heal_amount', return_value=(30, False)):
            for slot, skill in enumerate(skills):
                if slot == 3:
                    combat._php[uid] = 500
                packet, killed, damage = combat.cast_skill(area, uid, slot, target,
                    skill['data'], skill['forge'], skill['skill_id'])
                check('Slot ' + str(slot) + ' uses owner identity', packet['Caster'] == f'p:{uid}' and packet['Slot'] == slot)
                petnode = next(n for n in packet['Nodes'] if n['Name'] == 'PetAnimation')
                check('Slot ' + str(slot) + ' selects source animation', petnode['Animation'] == SKILLS[slot][1])
                dnode = next(n for n in packet['Nodes'] if n['Name'] == 'Damage')
                check('Slot ' + str(slot) + ' avoids unresolved player hit queue', dnode['Immediate'] is True)
                if slot == 3:
                    check('Mend heals owner instead of monster', dnode['Targets'] == [f'p:{uid}'] and combat.player_hp(uid) == 530 and damage == 0)
                else:
                    check('Damage uses selected enemy', dnode['Targets'] == [target] and damage == 20)
                props = next(p for p in skill['data'][1].values() if p['Name'] == 'PetAnimation')
                ctx = RenderContext(f'p:{uid}', slot=slot, target=target)
                check('Legacy/data engine PetAnimation parity slot ' + str(slot), render_node(ctx, props) == petnode)
                packets.append(packet)
            check('Cooldown admits once', combat.off_cooldown(uid, 2, 6500))
            check('Cooldown rejects duplicate', not combat.off_cooldown(uid, 2, 6500))
            combat.auto_engage(uid, area, target, skills[0]['data'], skills[0]['forge'], 2000, skills[0]['skill_id'])
            auto, _, _ = combat.execute_auto(area, uid, target, skills[0]['data'], skills[0]['forge'], skills[0]['skill_id'])
            check('Sustained auto keeps pet node and owner', auto[0]['Caster'] == f'p:{uid}' and any(n['Name']=='PetAnimation' for n in auto[0]['Nodes']))
            combat.cancel_player_actions(uid)
            check('Map/death cancellation drops sustained pet auto', not combat.auto_engaged(uid))

        for invalid in [dict(Animation='Idle',Duration=1), dict(Animation='Attack1',Duration=math.inf),
                        dict(Animation='Attack1',Duration=1,Speed=0), dict(Animation='Attack1',Duration=.01)]:
            try:
                battlepets.render_animation(invalid)
            except ValueError:
                checks.append('Malformed animation rejected')
            else:
                raise AssertionError(invalid)

        # Existing graphs retain their normal queued-damage wire shape.
        plain_data, plain_forge = graph('Attack1', 1, 2000, 1)
        plain_data[1]['2'].pop('Immediate')
        plain_data[1]['1'] = {'Name': 'PlayerAnimation', 'Animation': 'Attack1'}
        normal, _, _ = combat.cast_skill(area, uid, 1, target, plain_data, plain_forge)
        check('Ordinary class damage remains queued', 'Immediate' not in next(n for n in normal['Nodes'] if n['Name']=='Damage'))
        (LAB/'class_forge_init.json').write_text(json.dumps(reopened, indent=2), encoding='utf-8')
        (LAB/'class_seact.json').write_text(json.dumps(seact, indent=2), encoding='utf-8')
        result = dict(status='passed', checks=checks, packets=packets,
                      proof_boundary='SQLite Forge class persistence, real combat interpreter and packet contracts only. Native pet clips, live equipment admission, networking and rendered gameplay remain unproved.')
        (LAB/'class_validation.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
        print(json.dumps({'status':'passed','checks':len(checks),'class':'Daimyo Commander Lab','slots':5}))


if __name__ == '__main__':
    main()
