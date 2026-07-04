"""
Double-login guard: a second live session on the same character takes over cleanly, and the
first can't tear down the survivor when its socket later closes. The world room, _players and
combat state are all keyed by uid, so without the guard the loser's disconnect cleanup would
evict the winner (and a mid-fight reconnect would ghost the player).
"""
import asyncio
import json

import db
import seed
import game
import world
import server


class FakeWriter:
    """Minimal StreamWriter stand-in: swallow the s2c bytes, track close()."""
    def __init__(self):
        self.data = bytearray()
        self.closed = False

    def write(self, b):
        self.data.extend(b)

    async def drain(self):
        pass

    def close(self):
        self.closed = True


def _login_pkt(username, token):
    return json.dumps({"Cmd": "Login", "Params": ["0", username, token]}).encode()


def main():
    db.use_throwaway()
    seed.run()
    conn = db.connect()
    char = game.login(conn, "dupe", "pw")
    token = game.issue_token(conn, char["account_id"])
    uid = game.uid_for(char)

    async def run():
        w1 = FakeWriter()
        s1 = server.Session(w1)
        await server.dispatch(s1, w1, _login_pkt("dupe", token))
        assert server._players.get(uid) is s1, "first login owns _players[uid]"

        # place s1 in a world room so we can prove the second login evicts it
        world.join(s1.member, "battleon-1")
        assert any(m.uid == uid for m in world.members("battleon-1"))

        w2 = FakeWriter()
        s2 = server.Session(w2)
        await server.dispatch(s2, w2, _login_pkt("dupe", token))
        assert server._players.get(uid) is s2, "second login takes over _players[uid]"
        assert w1.closed, "old socket is closed on double login"
        assert not any(m.uid == uid for m in world.members("battleon-1")), \
            "old session is evicted from the world room (no stale entry)"

        # the superseded first session now disconnects: its cleanup must be a NO-OP for the
        # shared state (it no longer owns the uid), never evicting the live second session.
        server.cleanup_session(s1)
        assert server._players.get(uid) is s2, \
            "superseded session's cleanup must not evict the live session"

        # the real owner disconnecting DOES tear the uid down
        server.cleanup_session(s2)
        assert server._players.get(uid) is None, "owner cleanup removes the uid"

        print(f"double-login OK: uid={uid} takeover + world eviction + guarded cleanup")
        print("ALL DOUBLE-LOGIN TESTS PASSED")

    asyncio.run(run())


if __name__ == "__main__":
    main()
