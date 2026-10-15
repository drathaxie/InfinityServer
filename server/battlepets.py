"""Battlepet Forge prototype contracts. No pets/classes are enabled by importing this.

PetAnimation is a private-server visual node requiring the opt-in loader bridge.
Combat admission and capabilities must come from server-owned equipment/source
contracts before a live class can use it. The class lab supplies those fixtures.
"""
import math
import json
from pathlib import Path

STATES = ('Attack1', 'Attack2', 'Buff', 'PowerUp')
PALETTE = [
    {'Name': 'Animation', 'NameOverride': 'Pet Animation', 'ComponentType': 0,
     'ReturnType': 0, 'Options': list(STATES), 'AllowNulls': False,
     'Tooltip': 'Play a supported state on the equipped battlepet. Requires the battlepet loader bridge.'},
    {'Name': 'Duration', 'NameOverride': 'Duration (s)', 'ComponentType': 1,
     'ReturnType': 1, 'AllowNulls': False, 'Tooltip': 'Source clip duration at speed 1; returns to Idle or Walk.'},
    {'Name': 'Speed', 'NameOverride': 'Animation Speed', 'ComponentType': 1,
     'ReturnType': 1, 'AllowNulls': True, 'NullDisplay': '1', 'Tooltip': 'Pet clip playback speed.'},
]


def render_animation(props, target=None):
    state = props.get('Animation')
    duration = float(props.get('Duration', 0))
    speed = float(props.get('Speed', 1))
    if state not in STATES or not math.isfinite(duration) or not .05 <= duration <= 10:
        raise ValueError('Invalid battlepet animation or duration')
    if not math.isfinite(speed) or not .1 <= speed <= 4:
        raise ValueError('Invalid battlepet animation speed')
    return {'Name': 'PetAnimation', 'Animation': state, 'Duration': duration,
            'Speed': speed, 'Target': target or ''}


def admit(*, owner_alive, equipped_pet, required_pet, supported_states, state,
          same_instance, same_frame, target_alive, distance, max_range=8):
    """Pure admission check; caller must obtain every argument from server state.

    supported_states is a validated native capability contract, never inferred
    from a name/description or supplied by a client.
    """
    if not owner_alive:
        return 'Owner is defeated.'
    if equipped_pet != required_pet:
        return 'Equip the required battlepet.'
    if state not in supported_states:
        return 'Equipped pet has no validated animation for this command.'
    if not same_instance or not same_frame or not target_alive:
        return 'A living target in your current room is required.'
    if not math.isfinite(distance) or not 0 <= distance <= max_range:
        return 'Target is outside battlepet range.'
    return None


def check_skill(session, skill, target, *, order=None):
    """Revalidate equipment/capability and room on every manual/continued/auto cast."""
    import combat
    import game
    if order is None:
        order, _ = combat._walk_graph(skill.get('data') or [], skill.get('forge') or [])
    commands = [p for _, p in order if p.get('Name') == 'PetAnimation']
    if not commands:
        return None
    if not getattr(session.member, 'battlepet_v1', False):
        return 'Restart with the current Infinity loader to use battlepet skills.'
    if len(commands) != 1:
        return 'One pet command per skill is supported.'
    try:
        command = render_animation(commands[0], target)
        path = Path(__file__).resolve().parent.parent/'data/battlepet_capabilities.json'
        capabilities = json.loads(path.read_text(encoding='utf-8')) if path.is_file() else {}
        pet = game._equipped_rig(session.conn, session.char['id']).get('Pet') or {}
        cap = capabilities.get(str(pet.get('ID')), {})
        if not cap or not pet.get('Bundle') or pet['Bundle'].get('ID') != cap.get('bundle_id') or pet.get('PrefabName') != cap.get('prefab'):
            return 'Equip a validated battlepet to use this class.'
        row = session.conn.execute('SELECT raw FROM classes WHERE class_id=?', (session.equipped_class,)).fetchone()
        raw = json.loads(row['raw'] or '{}') if row else {}
        required = raw.get('battlepet', {}).get('pet_item_id', pet.get('ID'))
        duration = cap.get('states', {}).get(command['Animation'])
        if duration is None or abs(float(duration)-command['Duration']) > 1e-5:
            return 'Pet command does not match its validated source animation.'
        info = combat._moninfo.get((session.area, target), {})
        distance = math.hypot(float(info['x'])-float(session.member.x), float(info['y'])-float(session.member.y))
        return admit(owner_alive=combat.player_hp(session.member.uid)>0,
                     equipped_pet=pet.get('ID'), required_pet=required,
                     supported_states=cap['states'], state=command['Animation'],
                     same_instance=combat.valid_combat_target(session.area,target),
                     same_frame=info.get('frame')==session.member.frame,
                     target_alive=combat.valid_combat_target(session.area,target),
                     distance=distance, max_range=float(cap.get('max_range',8)))
    except (KeyError, ValueError, TypeError, OSError):
        return 'Battlepet target or capability data is unavailable.'


def legacy_packet(obj):
    """Older clients retain authoritative damage, but never receive unknown nodes."""
    if not isinstance(obj,dict) or not any(n.get('Name')=='PetAnimation' for n in (obj.get('Nodes') or []) if isinstance(n,dict)):
        return obj
    return dict(obj,Nodes=[n for n in obj['Nodes'] if not isinstance(n,dict) or n.get('Name')!='PetAnimation'])
