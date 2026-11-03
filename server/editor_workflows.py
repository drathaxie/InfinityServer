"""Staff browser authoring, with validation and optimistic edit conflicts."""
import hashlib
import json
import time
import urllib.parse

import db
import support_manager


class EditConflict(ValueError):
    pass


def version(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def lock_content(conn, kind):
    # Shared by native and browser writers. Also serializes MAX(id) allocation.
    conn.execute("INSERT INTO kv(k,v) VALUES(?,'0') ON CONFLICT(k) DO UPDATE SET v=kv.v",
                 ('editor_lock:' + kind,))


def store_scene(conn, raw, scene_id='', *, expected=None, actor=None):
    lock_content(conn, 'cutscene')
    if str(scene_id).isdigit() and int(scene_id) > 0:
        cid = int(scene_id)
        old = conn.execute('SELECT raw FROM cutscenes WHERE id=?', (cid,)).fetchone()
        if expected is not None and (old is None or version(old['raw']) != expected):
            raise EditConflict('This cutscene changed since you opened it. Reload before saving.')
    else:
        cid = int(conn.execute('SELECT COALESCE(MAX(id),0)+1 FROM cutscenes').fetchone()[0])
    conn.execute('INSERT INTO cutscenes(id,raw) VALUES(?,?) ON CONFLICT(id) DO UPDATE SET raw=excluded.raw',
                 (cid, raw))
    db.kv_set(conn, 'cache_revision:dialog', time.time_ns())
    if actor is not None:
        support_manager.audit(conn, 0, None, 'staff', actor, 'cutscene_saved', detail=str(cid))
    conn.commit()
    return {'ok': True, 'ID': cid, 'version': version(raw)}


def validate_scene(raw):
    if not isinstance(raw, str) or len(raw.encode('utf-8')) > 2_000_000:
        raise ValueError('Cutscene must be at most 2 MB.')
    scene = json.loads(raw)
    if not isinstance(scene, dict):
        raise ValueError('Cutscene must be an object.')
    frames = scene.get('frames')
    if not isinstance(frames, list) or not 1 <= len(frames) <= 2000:
        raise ValueError('Cutscene must contain 1 to 2000 pages.')
    for frame in frames:
        if not isinstance(frame, list) or len(frame) > 2000 or any(not isinstance(c, str) for c in frame):
            raise ValueError('Every page must contain a list of command strings.')
    for field in ('cutsceneName', 'cutsceneDescription'):
        if not isinstance(scene.get(field, ''), str):
            raise ValueError('Cutscene name and description must be text.')
    # Unknown envelope fields and command strings intentionally round-trip verbatim.
    return scene


def load_code(conn, code):
    row = conn.execute('SELECT * FROM redeem_codes WHERE LOWER(code)=LOWER(?)', (code,)).fetchone()
    if row is None:
        raise LookupError('Redeem code not found.')
    rewards = [dict(r) for r in conn.execute(
        'SELECT r.reward_type,r.reward_value,r.reward_qty,r.reward_field,i.name '
        'FROM redeem_code_rewards r LEFT JOIN items i ON r.reward_type=\'item\' AND i.id=r.reward_value '
        'WHERE r.code=? ORDER BY r.id', (row['code'],))]
    value = {'redeem': {k: row[k] for k in ('code', 'description', 'max_uses', 'active')}, 'rewards': rewards}
    value['version'] = version(json.dumps(value, sort_keys=True, separators=(',', ':')))
    return value


def handle(conn, key, method, args, actor):
    """Only called after current staff authentication and CSRF verification."""
    if isinstance(args, str):
        args = urllib.parse.parse_qs(args)
    get = lambda name, default='': args.get(name, [default])[0]
    if method == 'GET':
        if key == 'cutscene/list':
            out = []
            for row in conn.execute('SELECT id,raw FROM cutscenes ORDER BY id'):
                try:
                    scene = json.loads(row['raw'])
                    name = scene.get('cutsceneName') if isinstance(scene, dict) else None
                except (ValueError, TypeError):
                    name = None
                out.append({'ID': row['id'], 'name': name if isinstance(name, str) else f"Cutscene {row['id']}"})
            return out
        if key == 'cutscene/load':
            row = conn.execute('SELECT raw FROM cutscenes WHERE id=?', (int(get('ID')),)).fetchone()
            if row is None:
                raise LookupError('Cutscene not found.')
            return {'raw': row['raw'], 'version': version(row['raw'])}
        if key == 'cutscene/bundles':
            q = get('q').strip()
            return [dict(r) for r in conn.execute(
                'SELECT bundle_id AS id,name FROM asset_bundles WHERE LOWER(name) LIKE LOWER(?) '
                'OR CAST(bundle_id AS TEXT)=? ORDER BY name LIMIT 100', ('%' + q + '%', q))]
        if key == 'redeem/list':
            return [{'ID': r['code'], 'name': r['description'] or r['code']} for r in conn.execute(
                'SELECT code,description FROM redeem_codes ORDER BY code')]
        if key == 'redeem/load':
            return load_code(conn, get('ID'))
    if method == 'POST':
        if key == 'cutscene/save':
            raw, cid = get('json'), get('id')
            validate_scene(raw)
            if cid and (not cid.isdigit() or int(cid) <= 0):
                raise ValueError('Invalid cutscene ID.')
            return store_scene(conn, raw, cid, expected=get('version') if cid else None, actor=actor)
        if key == 'redeem/save':
            data = json.loads(get('json'))
            if not isinstance(data, dict) or not isinstance(data.get('redeem'), dict) or not isinstance(data.get('rewards'), list):
                raise ValueError('Invalid redeem code document.')
            rc = data['redeem']
            code = str(rc.get('code') or '').strip().upper()
            lock_content(conn, 'redeem')
            try:
                current = load_code(conn, code)
            except LookupError:
                current = None
            if (current and current['version'] != data.get('version')) or (not current and data.get('version')):
                raise EditConflict('This code changed since you opened it. Reload before saving.')
            if any(not isinstance(r, dict) for r in data['rewards']):
                raise ValueError('Invalid reward.')
            rewards = [{'type': r.get('reward_type'), 'value': r.get('reward_value'),
                        'quantity': r.get('reward_qty', 1), 'field': r.get('reward_field', 'ip25')}
                       for r in data['rewards']]
            ok, message = support_manager.save_code(conn, actor, {'code': code,
                'description': rc.get('description', ''), 'maxUses': rc.get('max_uses', 0),
                'active': rc.get('active', True), 'rewards': rewards})
            if not ok:
                raise ValueError(message)
            saved = load_code(conn, code)
            return {'ok': True, 'ID': saved['redeem']['code'], 'version': saved['version']}
    raise LookupError('Editor endpoint not found.')
