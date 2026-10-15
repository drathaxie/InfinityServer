"""Browser authoring contracts exercised over real HTTP and disposable databases."""
import http.client
import json
import threading
from urllib.parse import urlencode

import pytest

import editor_workflows
import game
import support_manager
import webapi
from test_review_regressions import conn, player, item, simultaneous  # noqa: F401


@pytest.fixture
def api(conn):
    staff = player(conn, 'editor_staff', 100)
    token = webapi._sign_session('editor_staff', 100)
    csrf = webapi._verify_session(token)['csrf']
    service = webapi._Server(('127.0.0.1', 0), webapi.Handler)
    thread = threading.Thread(target=service.serve_forever, daemon=True)
    thread.start()
    def call(path, form=None, *, authenticated=True, csrf_token=csrf, extra=None):
        headers = {'Cookie': f'{webapi.EDIT_COOKIE}={token}'} if authenticated else {}
        if form is not None:
            headers.update({'Content-Type': 'application/x-www-form-urlencoded', 'X-Staff-CSRF': csrf_token})
        headers.update(extra or {})
        client = http.client.HTTPConnection(*service.server_address, timeout=5)
        try:
            client.request('POST' if form is not None else 'GET', '/' + path,
                           urlencode(form) if form is not None else None, headers)
            response = client.getresponse()
            raw = response.read().decode()
            return response.status, json.loads(raw) if 'application/json' in response.getheader('Content-Type', '') else raw
        finally:
            client.close()
    yield call, staff
    service.shutdown()
    service.server_close()
    thread.join(timeout=5)


def scene_raw(name='Browser test'):
    return json.dumps({'cutsceneName': name, 'frames': [['Load{0|player|player}'],
                      ['UnknownFutureCommand{keep|&lt;literal&gt;}']], 'futureField': {'keep': True}}, indent=2)


def code_document(code='BROWSERTEST'):
    return {'redeem': {'code': code, 'description': 'Test code', 'max_uses': 1, 'active': 1},
            'rewards': [{'reward_type': 'gold', 'reward_value': 20, 'reward_qty': 1}]}


def test_browser_editor_gates_methods_csrf_and_current_role(conn, api):
    call, staff = api
    for prefix in ('cutscene', 'redeem'):
        assert call(prefix + '/Edit.aspx', authenticated=False)[0] == 302
        assert call(prefix + '/list', authenticated=False)[0] == 401
        assert call(prefix + '/save', {}, authenticated=False)[0] == 401
        assert call(prefix + '/save', {}, csrf_token='')[0] == 403
        assert call(prefix + '/save', {}, extra={'Origin': 'https://other.invalid'})[0] == 403
        status, html = call(prefix + '/Edit.aspx')
        assert status == 200 and '/editor/workflows.js' in html
        assert call(prefix + '/save')[0] == 404
    conn.execute('UPDATE characters SET access_level=0 WHERE id=?', (staff['id'],))
    conn.commit()
    assert call('CuTsCeNe/list')[0] == 401
    assert call('ReDeEm/save', {'json': json.dumps(code_document())})[0] == 401


def test_cutscene_http_preserves_commands_conflicts_and_audit(conn, api):
    call, _ = api
    raw = scene_raw()
    status, saved = call('cutscene/save', {'id': '', 'json': raw, 'actor': 'forged'})
    assert status == 200
    cid = saved['ID']
    status, loaded = call('cutscene/load?ID=' + str(cid))
    assert status == 200 and loaded['raw'] == raw
    assert call('cutscene/list')[1] == [{'ID': cid, 'name': 'Browser test'}]
    assert webapi.dialogger_load(conn, {'id': [str(cid)]}) == raw
    updated = scene_raw('Updated')
    form = {'id': cid, 'json': updated, 'version': loaded['version']}
    assert call('cutscene/save', form)[0] == 200
    assert call('cutscene/save', form)[0] == 409
    assert call('cutscene/load?ID=' + str(cid))[1]['raw'] == updated
    invalid = {'id': cid, 'json': '{"frames":[1]}', 'version': saved['version']}
    assert call('cutscene/save', invalid)[0] == 400
    assert conn.execute("SELECT actor_name FROM account_audit WHERE action='cutscene_saved'").fetchone()[0] == 'editor_staff'
    assert conn.execute("SELECT v FROM kv WHERE k='cache_revision:dialog'").fetchone()
    assert call('cutscene/load?ID=999999')[0] == 404
    assert call('cutscene/load?ID=bad')[0] == 400


def test_parallel_native_and_browser_cutscene_creation(conn):
    raw = scene_raw()
    functions = [lambda c: editor_workflows.store_scene(c, raw, actor='staff')['ID'] for _ in range(4)]
    functions += [lambda c: int(webapi.dialogger_save(c, {'json': [raw]})) for _ in range(4)]
    ids = simultaneous(functions)
    assert len(set(ids)) == 8
    assert conn.execute('SELECT COUNT(*) FROM cutscenes').fetchone()[0] == 8


def test_redeem_http_roundtrip_edit_conflict_and_redemption(conn, api):
    call, _ = api
    doc = code_document('browserTest')
    status, saved = call('redeem/save', {'json': json.dumps(doc), 'actor': 'forged'})
    assert status == 200 and saved['ID'] == 'BROWSERTEST'
    assert call('redeem/save', {'json': json.dumps(doc)})[0] == 409
    status, loaded = call('redeem/load?ID=browsertest')
    assert status == 200 and loaded['version'] == saved['version']
    loaded['redeem']['description'] = 'Edited'
    assert call('redeem/save', {'json': json.dumps(loaded)})[0] == 200
    assert call('redeem/save', {'json': json.dumps(loaded)})[0] == 409
    assert conn.execute("SELECT actor_name FROM account_audit WHERE action='redeem_code_saved'").fetchone()[0] == 'editor_staff'
    char = player(conn, 'code_player')
    assert game.redeem_code(conn, char, 'browsertest')['success']
    assert not game.redeem_code(conn, char, 'BROWSERTEST')['success']
    assert conn.execute('SELECT gold FROM characters WHERE id=?', (char['id'],)).fetchone()[0] == 1020


def test_invalid_rewards_never_replace_saved_code(conn, api):
    call, _ = api
    assert call('redeem/save', {'json': json.dumps(code_document())})[0] == 200
    _, original = call('redeem/load?ID=BROWSERTEST')
    for reward in ({'reward_type': 'gold', 'reward_value': -100},
                   {'reward_type': 'item', 'reward_value': 99999999},
                   {'reward_type': 'achievement', 'reward_value': 1, 'reward_field': 'access_level'},
                   {'reward_type': 'gold', 'reward_value': 1, 'reward_qty': 0}):
        invalid = {**original, 'rewards': [reward]}
        assert call('redeem/save', {'json': json.dumps(invalid)})[0] == 400
        assert call('redeem/load?ID=BROWSERTEST')[1] == original
    assert support_manager.save_code(conn, 'staff', {'code': 'BROWSERTEST', 'maxUses': -1,
        'rewards': [{'type': 'gold', 'value': 10}]})[0] is False


def test_editor_pickers_and_case_insensitive_legacy_code(conn, api):
    call, _ = api
    it = item(conn)
    conn.execute("INSERT INTO asset_bundles(bundle_id,name) VALUES(987654,'Browser Bundle')")
    conn.execute("INSERT INTO monsters(mon_id,name) VALUES(987654,'Browser NPC')")
    conn.execute("INSERT INTO redeem_codes(code) VALUES('MixedLegacy')")
    conn.commit()
    assert call('cutscene/bundles?q=987654')[1] == [{'id': 987654, 'name': 'Browser Bundle'}]
    assert any(r['id'] == 987654 for r in call('cutscene/npcs?q=Browser')[1])
    assert any(r['id'] == it['ID'] for r in call('redeem/items?q=Regression')[1])
    assert support_manager.save_code(conn, 'staff', {'code': 'MIXEDLEGACY',
        'rewards': [{'type': 'gold', 'value': 10}]})[0]
    assert conn.execute('SELECT COUNT(*) FROM redeem_codes').fetchone()[0] == 1
    assert call('redeem/load?ID=mixedlegacy')[1]['redeem']['code'] == 'MixedLegacy'
