"""Isolated Forge graph and battlepet admission experiment; never connects live.

The native protocol still attributes Damage to the owner. VisualIntent is a lab
record for a future client bridge, not a supported Forge node or network packet.
Run in a separate process because combat's state is intentionally process-global.
"""
import json
import os
from pathlib import Path
import sys
import tempfile
from contextlib import closing
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
os.environ['INFINITY_DB'] = 'sqlite'
sys.path.insert(0, str(ROOT / 'server'))
import db
import forge
import combat

LAB = ROOT / 'outputs/battlepet_forge_lab'
PET_ITEM = 6673  # Armored Daimyo Battlepet; source inspected in source_survey.json.
DATA = [{'0': {'Name': 'OnRequest', 'Slot': -1}}, {
    '2': {'Name': 'Cooldown', 'Slot': -1, 'CD': 2000},
    '6': {'Name': 'Damage', 'DamageType': 'Physical', 'Multiplier': 0.5,
          'Targets': {'id': '15'}},
    '15': {'Name': 'Target'}}]
FORGE = [{'0': {'X': 0., 'Y': 0.}, '2': {'X': 260., 'Y': 0.},
          '6': {'X': 520., 'Y': 0.}, '15': {'X': 520., 'Y': 220.}},
         {'0': {'Next': {'id': '2', 'Next': {'id': '6', 'Targets': {'id': '15'}}}}}]


class LabCompanion:
    """One owner's companion, with its own cooldown instead of a class HUD slot.

    Inputs here are fixtures. A production caller must supply server-owned area,
    frame, owner health, selected target and coordinates, never client assertions.
    """
    def __init__(self, owner, area, frame, pet_item, data, graph):
        self.owner, self.area, self.frame = owner, area, frame
        self.pet_item, self.data, self.graph = pet_item, data, graph
        self.next_attack = 0.
        self.active = True
        self.events = []

    def strike(self, target, *, now, area, frame, distance=0., equipped=True):
        # No target search: the owner must select a living enemy in this room.
        if not self.active:
            return {'status': 'inactive'}
        if not equipped or area != self.area or frame != self.frame or combat.player_hp(self.owner) <= 0:
            self.active = False
            return {'status': 'dismissed'}
        if not combat.valid_combat_target(area, target):
            return {'status': 'invalid_target'}
        if combat._moninfo.get((area, target), {}).get('frame') != frame:
            return {'status': 'wrong_frame'}
        if not 0 <= distance <= 8.:
            return {'status': 'outside_leash'}
        if now < self.next_attack:
            return {'status': 'cooldown'}
        self.next_attack = now + combat.skill_cooldown_ms(self.data, self.graph) / 1000.
        attack, killed, damage = combat.cast_skill(area, self.owner, -1, target, self.data, self.graph)
        # Cooldown is internal to the companion. Sending Slot=-1 to the native
        # HUD is intentionally not attempted by this experiment.
        event = dict(status='attack', owner=self.owner, pet_item=self.pet_item,
                     visual_intent={'state': 'Attack1', 'target': target},
                     attack=attack, killed=killed, damage=damage)
        self.events.append(event)
        return event


def main():
    LAB.mkdir(parents=True, exist_ok=True)
    checks = []

    def check(name, condition):
        if not condition:
            raise AssertionError(name)
        checks.append(name)

    # Unique SQLite file per run: no accidental import-time live DB or class edits.
    with tempfile.TemporaryDirectory(prefix='battlepet-forge-', dir=LAB) as scratch:
        db.DB_PATH = Path(scratch) / 'forge.sqlite'
        check('SQLite isolation', db.BACKEND == 'sqlite')
        with closing(db.connect()) as conn:
            with conn:
                db.initialize_connection(conn)
            reply = forge.sf_new_lib(conn, [0, 'Battlepet Strike Lab',
                'Owner-attributed damage fixture; pet animation bridge pending.',
                'iipet', json.dumps(DATA), json.dumps(FORGE)])
            sid = reply['Skill']
        with closing(db.connect()) as conn:
            reopened = forge.build_init(conn)['skills'][str(sid)]
            check('Forge graph survives database reopen', reopened['Data'] == DATA and reopened['ForgeData'] == FORGE)
            check('Library only, no class slots changed', conn.execute('SELECT COUNT(*) AS n FROM class_skills').fetchone()['n'] == 0)

        area, owner = 'battlepet-lab-instance', 910001
        combat.register_player(owner, 1000)
        combat.register_monster(area, 'm:1', 5000, frame='Enter')
        combat.register_monster(area, 'm:2', 5000, frame='Enter')
        combat.register_monster(area, 'm:3', 5000, frame='Other')
        pet = LabCompanion(owner, area, 'Enter', PET_ITEM, DATA, FORGE)

        def strike(target, now=100., **kwargs):
            return pet.strike(target, now=now, area=area, frame='Enter', **kwargs)

        check('No target does not attack', strike(None)['status'] == 'invalid_target')
        check('Player targets rejected', strike('p:910001')['status'] == 'invalid_target')
        check('Unregistered targets rejected', strike('m:999')['status'] == 'invalid_target')
        check('Other frame rejected', strike('m:3')['status'] == 'wrong_frame')
        check('Leash prevents remote damage', strike('m:1', distance=9.)['status'] == 'outside_leash')
        check('Rejected requests leave monster HP and cooldown unchanged', combat._mon[(area, 'm:1')] == 5000 and pet.next_attack == 0.)
        # Fix hit randomness only for reproducible fixture output; exercise real
        # target resolution, HP mutation and kill accounting in combat.cast_skill.
        with patch.object(combat, '_hit', return_value=(20, 0)):
            hit = strike('m:1')
            check('Damage reaches selected monster only', combat._mon[(area, 'm:1')] == 4980 and combat._mon[(area, 'm:2')] == 5000)
            check('Owner attribution retained', hit['attack']['Caster'] == f'p:{owner}')
            check('Visual intent names pet and enemy', hit['pet_item'] == PET_ITEM and hit['visual_intent'] == {'state': 'Attack1', 'target': 'm:1'})
            check('Companion cooldown blocks repeat', strike('m:1', now=100.1)['status'] == 'cooldown')
            check('No class cooldown consumed', not any(k[0] == owner for k in combat._cast_last))
            hit2 = strike('m:2', now=102.)
            check('Next attack follows changed selection', hit2['status'] == 'attack' and combat._mon[(area, 'm:2')] == 4980)
            combat._mon[(area, 'm:2')] = 10
            lethal = strike('m:2', now=104.)
            check('Lethal hit returns one owner kill', lethal['killed'] == ['m:2'] and combat._mon[(area, 'm:2')] == 0)
            check('Dead target cannot be credited twice', strike('m:2', now=106.)['status'] == 'invalid_target')
        check('Unequip dismisses companion', strike('m:1', now=108., equipped=False)['status'] == 'dismissed')
        check('Dismissed companion stays inactive', strike('m:1', now=110.)['status'] == 'inactive')
        for reason in ('map', 'frame', 'owner_death'):
            other = LabCompanion(owner, area, 'Enter', PET_ITEM, DATA, FORGE)
            if reason == 'owner_death':
                combat._php[owner] = 0
            result = other.strike('m:1', now=200., area='elsewhere' if reason == 'map' else area,
                                  frame='Other' if reason == 'frame' else 'Enter')
            check(reason + ' dismisses companion', result['status'] == 'dismissed')
        (LAB / 'forge_fixture.json').write_text(json.dumps(reopened, indent=2), encoding='utf-8')
        result = dict(status='passed', checks=checks, events=pet.events,
                      proof_boundary='Local SQLite Forge persistence and server combat admission only. No client animation, networking, rewards or live battlepet support proved.')
        (LAB / 'validation.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
        print(json.dumps(dict(status='passed', checks=len(checks), forge_fixture=str(LAB / 'forge_fixture.json'))))


if __name__ == '__main__':
    main()
