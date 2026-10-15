"""Server-owned random transformations and the room's Battle on! cheer."""
from copy import deepcopy
import json
import math
from pathlib import Path
import random
import time

import combat
import world

TRANSFORMATION_ITEM_ID = 200023
TRANSFORMATION_SKILL_ID = 90390
CHEER_ITEM_ID = 200025
CHEER_SKILL_ID = 90391
COOLDOWN_MS = 30_000
POOL_FILE = Path(__file__).resolve().parent.parent / 'data' / 'spellstone_transformations.json'
_room_cheers = {}


def transformation_pool(conn):
    """Read the curated list; catalog IDs resolve to canonical bundle/linkage metadata."""
    try:
        document = json.loads(POOL_FILE.read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        raise ValueError('The transformation list is unavailable.') from exc
    entries = document.get('monsters') if isinstance(document, dict) else None
    if not isinstance(entries, list) or not 1 <= len(entries) <= 200:
        raise ValueError('The transformation list must contain 1 to 200 monsters.')
    pool, seen = [], set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError('Each transformation must be a monster entry.')
        if 'monster_id' in entry:
            mid = entry['monster_id']
            if isinstance(mid, bool) or not isinstance(mid, int) or mid <= 0:
                raise ValueError('Transformation monster IDs must be positive integers.')
            row = conn.execute('SELECT name,linkage,bundle,scale FROM monsters WHERE mon_id=?', (mid,)).fetchone()
            if row is None:
                raise ValueError(f'Transformation monster {mid} was not found.')
            bundle = json.loads(row['bundle'] or 'null')
            name, linkage = row['name'], row['linkage']
            scale = entry.get('scale', row['scale'] or 1.0)
        else:
            name, linkage, bundle = entry.get('name'), entry.get('linkage'), entry.get('bundle')
            scale = entry.get('scale', 1.0)
        if not isinstance(bundle, dict) or not isinstance(bundle.get('ID'), int) or isinstance(bundle['ID'], bool) or bundle['ID'] <= 0:
            raise ValueError('Every transformation needs a valid bundle ID.')
        filename = bundle.get('Filename')
        if not isinstance(filename, str) or not filename or '..' in filename or ':' in filename or filename.startswith(('/', '\\')):
            raise ValueError('Every transformation needs a relative bundle filename.')
        if not isinstance(linkage, str) or not linkage.strip() or len(linkage) > 200:
            raise ValueError('Every transformation needs a prefab linkage.')
        try:
            scale = float(scale)
        except (ValueError, TypeError) as exc:
            raise ValueError('Invalid transformation scale.') from exc
        if not math.isfinite(scale) or not 0.05 <= scale <= 5:
            raise ValueError('Transformation scale must be between 0.05 and 5.')
        identity = (bundle['ID'], linkage)
        if identity in seen:
            raise ValueError('Duplicate transformation: ' + linkage)
        seen.add(identity)
        pool.append({'name': str(name or linkage), 'Bundle': deepcopy(bundle), 'Linkage': linkage, 'Scale': scale})
    return pool


def prepare_skill(conn, skill):
    """Select once for this cast request. All recipients receive the same native node."""
    if skill['skill_id'] != TRANSFORMATION_SKILL_ID:
        return skill
    selected = random.choice(transformation_pool(conn))
    prepared = deepcopy(skill)
    data = prepared['data']
    nodes = data[1] if isinstance(data, list) and len(data) > 1 and isinstance(data[1], dict) else {}
    found = False
    for props in nodes.values():
        if isinstance(props, dict) and props.get('Name') == 'MonTransform' and not props.get('detransform'):
            props.update({key: selected[key] for key in ('Bundle', 'Linkage', 'Scale')})
            found = True
    if not found:
        raise ValueError('The transformation skill has no transformation node.')
    return prepared


def cheer_ready(member):
    now = time.monotonic()
    # Room entries live only for the cooldown window, even if everyone leaves.
    for key, until in list(_room_cheers.items()):
        if until <= now:
            _room_cheers.pop(key, None)
    return (member.area, member.frame) not in _room_cheers


def group_cheer(member):
    """Send one fixed line per eligible hero to the same visible room, including self."""
    if not cheer_ready(member):
        return
    _room_cheers[(member.area, member.frame)] = time.monotonic() + COOLDOWN_MS / 1000
    viewers = [m for m in world.members(member.area) if m.frame == member.frame]
    participants = [m for m in viewers if combat.player_hp(m.uid) > 0 and not combat.in_combat(m.uid, member.area)]
    for participant in participants:
        packet = {'Cmd': 'chatm', 'msg': 'Battle on!', 'Name': participant.name,
                  'channel': 'zone', 'ID': participant.uid}
        for viewer in viewers:
            world.send(viewer, {'Cmd': 'emotea', 'userID': participant.uid, 'strEmote': 'Em_Cheer'})
            world.send(viewer, packet)
