"""Regression coverage for the September review; real disposable DBs, HTTP and TCP."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
import threading
import time
from urllib import request, parse, error

import pytest

import account_manager
import combat
import db
import game
import migrate_to_pg
import server
import support_manager
import webapi
import world
from handlers.context import Session, _players, load_shop


@pytest.fixture
def conn(monkeypatch):
    monkeypatch.setattr(game, "_dev_users", lambda: {"reserved_staff"})
    monkeypatch.setattr(webapi, "EDIT_SECRET", b"regression-test-only")
    monkeypatch.setattr(webapi, "ACCOUNT_SECRET", b"regression-account-only")
    db.use_throwaway()
    db.init()
    c = db.connect()
    yield c
    c.close()


def player(conn, name="regression_player", access=0):
    char = game.login(conn, name, "test-password")
    conn.execute("UPDATE characters SET access_level=?,gold=1000,coins=10 WHERE id=?", (access, char["id"]))
    conn.commit()
    return conn.execute("SELECT * FROM characters WHERE id=?", (char["id"],)).fetchone()


def item(conn, iid=987651, **fields):
    value = {"ID": iid, "Name": "Regression item", "ItemType": 1, "EquipSpot": 0,
             "Cost": 10, "Coins": False, "StackSize": 999, "UpgradeOnly": False, **fields}
    db.store_item(conn, value, replace=True)
    conn.commit()
    return value


def offer(conn, it, stock=-1, shop=987650):
    conn.execute("INSERT INTO shop_items(shop_id,shop_item_id,item_id,cost,coins,quantity_remain) "
                 "VALUES(?,1,?,1,0,?)", (shop, it["ID"], stock))
    conn.commit()
    return [0, shop, 1]


def promo(conn, code="RACE", limit=0, rewards=(("gold", 10),)):
    conn.execute("INSERT INTO redeem_codes(code,max_uses) VALUES(?,?)", (code, limit))
    for kind, value in rewards:
        conn.execute("INSERT INTO redeem_code_rewards(code,reward_type,reward_value) VALUES(?,?,?)",
                     (code, kind, value))
    conn.commit()


def simultaneous(functions):
    gate = threading.Barrier(len(functions))
    def run(fn):
        c = db.connect()
        try:
            gate.wait(timeout=10)
            return fn(c)
        finally:
            c.close()
    with ThreadPoolExecutor(max_workers=len(functions)) as pool:
        return list(pool.map(run, functions))


def test_redemption_is_atomic_on_invalid_bundle(conn):
    char = player(conn)
    it = item(conn)
    promo(conn, rewards=(("gold", 50), ("item", it["ID"]), ("item", 99999999)))
    for _ in range(2):
        assert not game.redeem_code(conn, char, "RACE")["success"]
    assert conn.execute("SELECT gold FROM characters WHERE id=?", (char["id"],)).fetchone()[0] == 1000
    assert conn.execute("SELECT COUNT(*) FROM char_items WHERE item_id=?", (it["ID"],)).fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM redeem_code_uses").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM redeem_code_claims").fetchone()[0] == 0


def test_same_account_concurrent_claims(conn):
    char = player(conn)
    promo(conn)
    replies = simultaneous([lambda c: game.redeem_code(c, char, "rAcE")["success"] for _ in range(8)])
    assert sum(replies) == 1
    assert conn.execute("SELECT COUNT(*) FROM redeem_code_uses").fetchone()[0] == 1
    assert conn.execute("SELECT gold FROM characters WHERE id=?", (char["id"],)).fetchone()[0] == 1010


def test_concurrent_global_redemption_limit(conn):
    chars = [player(conn, f"race_player_{n}") for n in range(6)]
    promo(conn, limit=2)
    conn.commit()
    replies = simultaneous([lambda c, char=char: game.redeem_code(c, char, "RACE")["success"] for char in chars])
    assert sum(replies) == 2
    assert conn.execute("SELECT COUNT(*) FROM redeem_code_uses").fetchone()[0] == 2


def test_buyback_competing_requests(conn):
    char = player(conn)
    it = item(conn)
    bid = conn.execute("INSERT INTO ac_item_buybacks(account_id,char_id,item_id,remaining_quantity,unit_price,sold_at) "
                       "VALUES(?,?,?,1,10,0) RETURNING id", (char["account_id"], char["id"], it["ID"])).fetchone()[0]
    conn.commit()
    replies = simultaneous([lambda c: account_manager.buy_back(c, char["account_id"], bid)[0] for _ in range(6)])
    assert sum(replies) == 1
    assert conn.execute("SELECT coins FROM characters WHERE id=?", (char["id"],)).fetchone()[0] == 0
    assert conn.execute("SELECT remaining_quantity FROM ac_item_buybacks WHERE id=?", (bid,)).fetchone()[0] == 0


def test_shop_gate_price_stock_and_membership(conn):
    char = player(conn)
    it = item(conn)
    params = offer(conn, it, stock=1)
    row = conn.execute("SELECT * FROM shop_items WHERE shop_id=?", (params[1],)).fetchone()
    assert game.shop_listing(conn, row)["Cost"] == 10
    assert game.buy(conn, char, params)["Cost"] == 10  # stale offer column is 1
    assert not game.buy(conn, char, params)["Success"]
    item(conn, UpgradeOnly=True)
    conn.execute("UPDATE shop_items SET quantity_remain=-1")
    conn.commit()
    assert not game.buy(conn, char, params)["Success"]
    game.set_membership(conn, char["id"], 3)
    conn.commit()
    assert game.buy(conn, char, params)["Success"]
    dev = offer(conn, it, shop=2722)
    assert load_shop(conn, [2722])["shop"]["items"] == []
    assert not game.buy(conn, char, dev)["Success"]
    conn.execute("UPDATE characters SET access_level=100 WHERE id=?", (char["id"],))
    conn.commit()
    assert game.buy(conn, char, dev)["Cost"] == 0


def test_competing_shop_purchases_claim_final_stock(conn):
    chars = [player(conn, f"shop_player_{i}") for i in range(4)]
    params = offer(conn, item(conn), stock=1)
    replies = simultaneous([lambda c, char=char: game.buy(c, char, params)["Success"] for char in chars])
    assert sum(replies) == 1
    assert conn.execute("SELECT SUM(gold) FROM characters").fetchone()[0] == 3990


def test_reserved_names_never_grant_roles(conn):
    assert game.login(conn, "reserved_staff", "pw") is None
    char = player(conn)
    assert not account_manager.change_username(conn, char["account_id"], "test-password", "reserved_staff")[0]
    conn.execute("UPDATE accounts SET username='reserved_staff' WHERE id=?", (char["account_id"],))
    conn.commit()
    assert game.authenticate(conn, "reserved_staff", "test-password")["access"] == 0
    assert game.login(conn, "reserved_staff", "test-password")["access_level"] == 0


def test_cookies_revoke_on_password_demotion_and_logout(conn):
    char = player(conn, access=100)
    account = webapi._sign_account_session(char["account_id"], char["name"])
    staff = webapi._sign_session(char["name"], 100)
    assert webapi._verify_account_session(account)
    assert webapi._verify_session(staff)
    conn.execute("UPDATE characters SET access_level=0 WHERE id=?", (char["id"],))
    conn.commit()
    assert webapi._verify_session(staff) is None
    assert account_manager.change_password(conn, char["account_id"], "test-password", "replacement-password")[0]
    assert webapi._verify_account_session(account) is None
    fresh = webapi._sign_account_session(char["account_id"], char["name"])
    webapi._revoke_browser_session(fresh)
    assert webapi._verify_account_session(fresh) is None


def test_membership_expiry_and_extension(conn, monkeypatch):
    char = player(conn)
    now = time.time()
    monkeypatch.setattr(game.time, "time", lambda: now)
    assert support_manager.grant(conn, "test-staff", char["id"], "membership", 3, 1, "test grant")[0]
    updated = conn.execute("SELECT * FROM characters WHERE id=?", (char["id"],)).fetchone()
    assert game.membership_days(updated) == 3
    monkeypatch.setattr(game.time, "time", lambda: now + 4 * 86400)
    assert game.membership_days(updated) == 0


@pytest.fixture
def http_api(conn):
    httpd = webapi._Server(("127.0.0.1", 0), webapi.Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    def call(path, form, headers=None):
        req = request.Request(f"http://127.0.0.1:{httpd.server_address[1]}/{path}",
                              data=parse.urlencode(form).encode(), headers=headers or {}, method="POST")
        try:
            with request.urlopen(req, timeout=5) as response:
                return response.status, response.read()
        except error.HTTPError as exc:
            return exc.code, exc.read()
    yield call
    httpd.shutdown()
    httpd.server_close()
    thread.join(timeout=5)


@pytest.mark.parametrize("path,form", [
    ("tweak/CreateNewApop", {"npcID": 0}),
    ("TwEaK/DiAlOgGeRsAvE", {"id": 987654, "json": '{"test":true}'})])
def test_authoring_http_requires_current_staff(conn, http_api, path, form):
    char = player(conn)
    token = game.issue_token(conn, char["account_id"])
    headers = {"Authorization": f"Bearer {char['id']}:{token}"}
    assert http_api(path, form)[0] == 401
    assert http_api(path, form, headers)[0] == 401
    assert conn.execute("SELECT COUNT(*) FROM cutscenes").fetchone()[0] == 0
    conn.execute("UPDATE characters SET access_level=100 WHERE id=?", (char["id"],))
    conn.commit()
    assert http_api(path, form, headers)[0] == 200
    conn.execute("UPDATE characters SET access_level=0 WHERE id=?", (char["id"],))
    conn.commit()
    assert http_api(path, form, headers)[0] == 401


class Writer:
    def __init__(self):
        self.data, self.closed = [], False
    def write(self, data):
        self.data.append(data)
    async def drain(self):
        pass
    def close(self):
        self.closed = True


def test_socket_relogin_and_revoke(conn):
    a, b = player(conn, "socket_a"), player(conn, "socket_b")
    ta, tb = game.issue_token(conn, a["account_id"]), game.issue_token(conn, b["account_id"])
    async def run():
        w = Writer()
        s = Session(w)
        async def send(cmd, params):
            await server.dispatch(s, w, json.dumps({"Cmd": cmd, "Params": params}).encode())
        try:
            await send("Login", ["", "socket_a", ta])
            uid = s.member.uid
            world.join(s.member, "regression_room")
            s.area = "regression_room"
            await send("Login", ["", "socket_b", tb])
            assert s.char["id"] == a["id"]
            combat._php[uid] = 10
            await send("resPlayerTimed", [])
            assert combat.player_hp(uid) == 10
            conn.execute("UPDATE accounts SET session_token=NULL WHERE id=?", (a["account_id"],))
            conn.commit()
            await send("rest", [])
            assert w.closed and combat.player_hp(uid) == 10
        finally:
            server.cleanup_session(s)
        assert uid not in _players
        assert uid not in world._rooms.get("regression_room", {})
    asyncio.run(run())


def test_cast_context_owner_slot_node_area_and_expiry(monkeypatch):
    combat.register_player(987660, 100)
    cast = combat._Cast("regression_room", 987660, 1, "m:1", [("1", {"Name": "Range"})], {})
    ctx = combat._new_ctx(cast)
    for uid, area, params in [(987661, "regression_room", [1, ctx, "Range", "false"]),
                               (987660, "other", [1, ctx, "Range", "false"]),
                               (987660, "regression_room", [2, ctx, "Range", "false"]),
                               (987660, "regression_room", [1, ctx, "Hitbox", "false"])]:
        assert combat.resume_cast(ctx, params, uid=uid, area=area) == ([], [], 0)
        assert ctx in combat._casts
    result = combat.resume_cast(ctx, [1, ctx, "Range", "false"], uid=987660, area="regression_room")
    assert result[0] and ctx not in combat._casts
    ctx = combat._new_ctx(cast)
    monkeypatch.setattr(combat.time, "time", lambda: cast.context_expires + 1)
    assert combat.resume_cast(ctx, [1, ctx, "Range", "false"], uid=987660, area="regression_room") == ([], [], 0)
    assert ctx not in combat._casts
    combat.forget_player(987660)


def test_hitstream_targets_area_frame_and_cleanup():
    uid, area = 987662, "regression_stream"
    combat.register_player(uid, 100)
    combat.register_monster(area, "m:1", 100000, frame="Enter")
    combat.start_hitstream(area, uid, 1, 20)
    assert combat.player_hitstream_hit(uid, 1, ["m:404"])[0] is None
    assert (area, "m:404") not in combat._mon
    assert combat.player_hitstream_hit(uid, 1, ["m:1"], current_area="other")[0] is None
    assert combat.player_hitstream_hit(uid, 1, ["m:1"], frame="other")[0] is None
    assert combat.player_hitstream_hit(uid, 1, ["m:1"], current_area=area, frame="Enter")[0]
    combat.cancel_player_actions(uid)
    assert combat.player_hitstream_hit(uid, 1, ["m:1"])[0] is None
    assert not any(k[1] == uid for k in combat._hitstream_last)
    combat.start_hitstream(area, uid, 1, 20)
    combat.forget_player(uid)
    assert (uid, 1) not in combat._hitstreams


def test_revive_requires_death_and_elapsed_timer(monkeypatch):
    uid = 987663
    combat.register_player(uid, 100)
    combat._php[uid] = 10
    assert combat.revive_player(uid, "player") is None
    combat.lethal_self_packet(uid)
    combat.player_death_packet(uid, f"p:{uid}")
    assert combat.rest_player(uid, "player") is None
    assert combat.revive_player(uid, "player") is None
    eligible = combat._revive_after[uid]
    monkeypatch.setattr(combat.time, "time", lambda: eligible + 0.01)
    assert combat.revive_player(uid, "player")["HP"] == 100
    assert combat.revive_player(uid, "player") is None
    combat.forget_player(uid)


def test_monster_aura_cleanup_is_scoped():
    for area in ("regression_a", "regression_b"):
        combat.apply_aura(area, "Stunned", ["m:987664"], "p:1")
    combat.death_packets("regression_a", "m:987664", 1)
    assert combat.aura_active("regression_b", "m:987664", "Stunned")
    combat.respawn_packet("regression_a", "m:987664")
    assert combat.aura_active("regression_b", "m:987664", "Stunned")
    combat.drop_auras_for("m:987664", "regression_b")


@pytest.mark.parametrize("payload", [b"x" * 65, b"[]\0", b'{"Cmd":[],"Params":{}}\0', b""])
def test_bounded_unauthenticated_socket_does_not_open_db(monkeypatch, payload):
    monkeypatch.setattr(server, "MAX_FRAME_BYTES", 64)
    monkeypatch.setattr(server, "HANDSHAKE_SECONDS", 0.1)
    def forbidden():
        raise AssertionError("Unauthenticated malformed/idle peer opened a database")
    monkeypatch.setattr(db, "connect", forbidden)
    async def run():
        listener = await asyncio.start_server(server.handle_client, "127.0.0.1", 0)
        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", listener.sockets[0].getsockname()[1])
            writer.write(payload)
            await writer.drain()
            assert await asyncio.wait_for(reader.read(), 2) == b""
            writer.close()
            await writer.wait_closed()
        finally:
            listener.close()
            await listener.wait_closed()
    asyncio.run(run())


def test_migration_manifest_covers_schema_and_rejects_extra(conn):
    if db.BACKEND != "sqlite":
        pytest.skip("SQLite source manifest; PostgreSQL copy is exercised separately")
    assert set(migrate_to_pg.copy_order(conn)) == migrate_to_pg.source_tables(conn)
    conn.execute("CREATE TABLE future_table(id INTEGER)")
    with pytest.raises(ValueError, match="extra"):
        migrate_to_pg.copy_order(conn)


def test_postgres_context_rolls_back_on_exception():
    class Raw:
        committed = rolled_back = False
        def commit(self): self.committed = True
        def rollback(self): self.rolled_back = True
    raw = Raw()
    with pytest.raises(ValueError):
        with db._PgConnection(raw):
            raise ValueError("deliberate")
    assert raw.rolled_back and not raw.committed


def test_private_cast_requests_are_never_broadcast(conn, monkeypatch):
    from handlers import combat_cmds
    import forge
    char = player(conn)
    owner, bystander = Writer(), Writer()
    s = Session(owner)
    s.char, s.area = char, "private_cast_room"
    uid = game.uid_for(char)
    s.member = world.Member(uid, char["name"], {}, owner)
    observer = world.Member(uid + 1, "observer", {}, bystander)
    world.join(s.member, s.area)
    world.join(observer, s.area)
    combat.register_player(uid, 100)
    combat.register_monster(s.area, "m:1", 100000, frame="Enter")
    private = {"Cmd": "igai", "ContextId": "test-private-context", "Response": {"Name": "Range"}}
    monkeypatch.setattr(forge, "skill_for_slot", lambda c, cls, slot:
                        {"data": None, "forge": None, "skill_id": 123} if slot == 1 else None)
    monkeypatch.setattr(combat, "begin_cast", lambda *args: ([private], [], 0))
    monkeypatch.setattr(combat, "resume_cast", lambda *args, **kwargs: ([private], [], 0))
    async def run():
        await combat_cmds.begin_cast(s, owner, "gar", [1, "m:1"], {})
        await combat_cmds.continue_cast(s, owner, "gai", [1, "test-private-context", "Range"], {})
    try:
        asyncio.run(run())
        assert sum(json.loads(raw.rstrip(b'\0'))["Cmd"] == "igai" for raw in owner.data) == 2
        assert not any(json.loads(raw.rstrip(b'\0'))["Cmd"] == "igai" for raw in bystander.data)
    finally:
        combat.forget_player(uid)
        world.leave(s.member)
        world.leave(observer)
        s.close()


def test_failed_buyback_grant_rolls_back_stock_and_currency(conn, monkeypatch):
    char = player(conn)
    it = item(conn)
    bid = conn.execute("INSERT INTO ac_item_buybacks(account_id,char_id,item_id,remaining_quantity,unit_price,sold_at) "
                       "VALUES(?,?,?,1,10,0) RETURNING id", (char["account_id"], char["id"], it["ID"])).fetchone()[0]
    conn.commit()
    def failed(*args):
        raise RuntimeError("injected grant failure")
    monkeypatch.setattr(game, "_grant_item", failed)
    with pytest.raises(RuntimeError):
        account_manager.buy_back(conn, char["account_id"], bid)
    assert conn.execute("SELECT coins FROM characters WHERE id=?", (char["id"],)).fetchone()[0] == 10
    assert conn.execute("SELECT remaining_quantity FROM ac_item_buybacks WHERE id=?", (bid,)).fetchone()[0] == 1
