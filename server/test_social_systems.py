"""End-to-end smoke of the playtest social/player commands added for the new client:
party (pi/pa), friends (requestFriend/addFriend/deleteFriend), guild (gc/gia/gmotd),
inspectPlayer, genderSwap. Drives everything through server.dispatch() like the client.
"""
import asyncio
import json

import db
import seed
import world
import server
import game
import friends as friendsvc
import guilds


class FakeWriter:
    def __init__(self):
        self.data = bytearray()
        self.closed = False

    def write(self, b):
        self.data.extend(b)

    async def drain(self):
        pass

    def close(self):
        self.closed = True


def _pkt(cmd, *params):
    return json.dumps({"Cmd": cmd, "Params": [str(p) for p in params]}).encode()


def _cmds(writer):
    """The Cmd of every framed JSON packet written to this socket."""
    out = []
    for chunk in bytes(writer.data).split(b"\x00"):
        if not chunk:
            continue
        try:
            out.append(json.loads(chunk)["Cmd"])
        except Exception:
            pass
    return out


def _packets(writer):
    """Decoded framed JSON packets written to this socket."""
    out = []
    for chunk in bytes(writer.data).split(b"\x00"):
        if chunk:
            out.append(json.loads(chunk))
    return out


def _mkchar(conn, name):
    conn.execute("INSERT INTO accounts(username, password) VALUES(?, 'x')", (name,))
    acc = conn.execute("SELECT id FROM accounts WHERE username=?", (name,)).fetchone()["id"]
    cid = conn.execute(
        "INSERT INTO characters(account_id, name, gender, gold, coins, level, class_id) "
        "VALUES(?,?,?,0,0,5,0) RETURNING id", (acc, name, "M")).fetchone()["id"]
    conn.commit()
    return conn.execute("SELECT * FROM characters WHERE id=?", (cid,)).fetchone()


def _session(char, writer, area):
    s = server.Session(writer)
    s.char = char
    s.area = area
    uid = 1_000_000 + char["id"]
    s.member = world.Member(uid, char["name"], {"intAccessLevel": 0}, writer)
    s.member.frame = "Enter"
    s.area = area
    world.join(s.member, area)
    return s


def main():
    db.use_throwaway()
    seed.run()
    area = "socialtest-1"
    world._rooms.pop(area, None)
    conn = db.connect()
    alice_c = _mkchar(conn, "alice2")
    bob_c = _mkchar(conn, "bob2")

    wa, wb = FakeWriter(), FakeWriter()
    sa = _session(alice_c, wa, area)
    sb = _session(bob_c, wb, area)

    async def run():
        # --- party: alice invites bob, bob accepts -> both get pa (ResponsePartyData) ---
        await server.dispatch(sa, wa, _pkt("pi", "bob2"))
        assert "pi" in _cmds(wb), "bob receives party invite"
        await server.dispatch(sb, wb, _pkt("pa", sa.member.uid))
        import parties
        assert parties.party_of(sb.member.uid)[0] is not None, "bob is in a party"
        assert "pa" in _cmds(wa) and "pa" in _cmds(wb), "both get ResponsePartyData"

        # --- friends: bob accepts a request from alice -> mutual + both get addFriend ---
        wa.data.clear(); wb.data.clear()
        await server.dispatch(sb, wb, _pkt("addFriend", "alice2"))
        assert friendsvc.are_friends(conn, alice_c["id"], bob_c["id"]), "mutual friendship stored"
        assert "addFriend" in _cmds(wb), "accepter gets addFriend"
        assert "addFriend" in _cmds(wa), "requester (online) gets addFriend"
        # delete
        await server.dispatch(sb, wb, _pkt("deleteFriend", alice_c["id"]))
        assert not friendsvc.are_friends(conn, alice_c["id"], bob_c["id"]), "friendship removed"

        # --- guild: alice creates, bob joins via gia, motd broadcasts ---
        wa.data.clear(); wb.data.clear()
        await server.dispatch(sa, wa, _pkt("gc", "The Testers"))
        assert "newGuild" in _cmds(wa), "creator gets newGuild"
        gid = sa.conn.execute("SELECT guild_id FROM characters WHERE id=?",
                              (alice_c["id"],)).fetchone()["guild_id"]
        assert gid, "alice has a guild"
        # bob accepts an invite from alice
        await server.dispatch(sb, wb, _pkt("gia", "alice2"))
        bob_gid = sb.conn.execute("SELECT guild_id FROM characters WHERE id=?",
                                  (bob_c["id"],)).fetchone()["guild_id"]
        assert bob_gid == gid, "bob joined alice's guild"
        assert "gAddMem" in _cmds(wa), "existing member told about the newcomer"
        assert "newGuild" in _cmds(wb), "joiner gets the guild"
        # Guild membership remains native playerInfo.guild, but the retired custom nameplate-tag
        # transport deliberately carries blanks so older PC loaders clear their local tag cache.
        init = game.build_init_player(conn, sa.char)
        assert init["user"]["guildName"] == "" and init["user"]["guildTagColor"] == "", \
            "custom guild nameplate tag is disabled"
        assert "tagShop" not in init, "custom guild tag shop is not served"
        wa.data.clear()
        await server.dispatch(sa, wa, _pkt("cmd", "tagcolor"))
        assert "chatm" in _cmds(wa), "retired tag command reports unavailable"
        # motd (alice is leader)
        wa.data.clear(); wb.data.clear()
        await server.dispatch(sa, wa, _pkt("gmotd", "Welcome!"))
        assert "gMOTD" in _cmds(wb), "guildmate gets the MOTD broadcast"

        # --- guild hall: leader decorates (housesave routes to the guild), member visits/can't ---
        wa.data.clear(); wb.data.clear()
        await server.dispatch(sa, wa, _pkt("cmd", "guildhall"))
        assert sa.guildhall_gid == gid, "alice (leader) is inside her guild hall"
        layout = json.dumps([{"ItemID": 1, "x": 5, "y": 5}])
        await server.dispatch(sa, wa, _pkt("housesave", game.GUILDHALL_ITEM_ID, "Enter", layout))
        assert "houseSave" in _cmds(wa), "leader gets a houseSave ack"
        assert guilds.hall_layout(conn, gid).get("Enter"), "hall layout persisted to the GUILD"
        # bob (member, not leader) joins the SAME shared hall instance and can't overwrite it
        wb.data.clear()
        await server.dispatch(sb, wb, _pkt("cmd", "guildhall"))
        assert sb.guildhall_gid == gid, "bob shares the guild hall instance"
        assert sb.area == sa.area, "both in the same per-guild hall room"
        await server.dispatch(sb, wb, _pkt("housesave", game.GUILDHALL_ITEM_ID, "Enter", "[]"))
        assert guilds.hall_layout(conn, gid).get("Enter"), "a non-leader can't wipe the hall"

        # --- guild leave: bob leaves (alice stays leader), then alice leaves -> empty guild disbands ---
        await server.dispatch(sb, wb, _pkt("cmd", "gleave"))
        assert not sb.conn.execute("SELECT guild_id FROM characters WHERE id=?",
                                   (bob_c["id"],)).fetchone()["guild_id"], "bob left the guild"
        await server.dispatch(sa, wa, _pkt("cmd", "gleave"))
        assert conn.execute("SELECT 1 FROM guilds WHERE id=?", (gid,)).fetchone() is None, \
            "the now-empty guild was disbanded"

        # --- inspectPlayer: alice inspects bob -> gets an items packet ---
        wa.data.clear()
        await server.dispatch(sa, wa, _pkt("inspectPlayer", sb.member.uid))
        assert "inspectPlayer" in _cmds(wa), "inspect returns an items packet"

        # --- genderSwap: alice M -> F, broadcast to the room ---
        wa.data.clear()
        await server.dispatch(sa, wa, _pkt("genderSwap"))
        new_g = sa.conn.execute("SELECT gender FROM characters WHERE id=?",
                                (alice_c["id"],)).fetchone()["gender"]
        assert new_g == "F", "gender flipped to F"
        assert "genderSwap" in _cmds(wa), "genderSwap broadcast"

        # --- custom titles through AE's native picker/save protocol ---
        import titles as titlesvc

        def _reload(c):
            return conn.execute("SELECT * FROM characters WHERE id=?", (c["id"],)).fetchone()

        assert "Hero" in titlesvc.available_titles(sa.char), "custom title is available to all players"

        wa.data.clear()
        await server.dispatch(sa, wa, _pkt("getPlayerTitles"))
        assert "getPlayerTitles" in _cmds(wa), "getPlayerTitles returns a list"
        response = next(p for p in _packets(wa) if p["Cmd"] == "getPlayerTitles")
        assert response["Titles"] == titlesvc.available_titles(sa.char), \
            "native picker response carries the custom catalog"

        wa.data.clear(); wb.data.clear()
        await server.dispatch(sa, wa, _pkt("savePlayerTitle", "Hero"))
        assert "savePlayerTitle" in _cmds(wa), "setter gets ResponseSavePlayerTitle"
        assert "AreaAdd" in _cmds(wb), "room-mate gets a nameplate refresh (AreaAdd)"
        assert titlesvc.selected(_reload(alice_c)) == "Hero", "title persisted to prefs"
        assert game.build_init_player(conn, _reload(alice_c))["user"]["Title"] == "Hero", \
            "Title rides the user object"

        # a title the character doesn't own is silently ignored (server-authoritative)
        await server.dispatch(sa, wa, _pkt("savePlayerTitle", "Game Master"))
        assert titlesvc.selected(_reload(alice_c)) == "Hero", "spoofed title ignored"

        # clearing ('No title') removes it
        await server.dispatch(sa, wa, _pkt("savePlayerTitle", ""))
        assert titlesvc.selected(_reload(alice_c)) == "", "title cleared"

        print("ALL SOCIAL SYSTEM TESTS PASSED")

    asyncio.run(run())


if __name__ == "__main__":
    main()
