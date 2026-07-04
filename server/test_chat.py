"""
Chat channel routing: party/guild chat must NOT leak to the physical room. Until party/guild
membership is modelled, those channels echo to the sender only (nobody else is in your party);
zone chat still reaches everyone sharing the room.
"""
import asyncio
import json

import db
import seed
import world
import server


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


def _chat(msg, channel):
    return json.dumps({"Cmd": "chat", "Params": [msg, channel]}).encode()


def main():
    db.use_throwaway()
    seed.run()
    area = "chattest-1"
    world._rooms.pop(area, None)

    wa = FakeWriter()
    sa = server.Session(wa)
    sa.member = world.Member(101, "alice", {}, wa)
    sa.area = area
    world.join(sa.member, area)

    wb = FakeWriter()
    sb = server.Session(wb)
    sb.member = world.Member(102, "bob", {}, wb)
    sb.area = area
    world.join(sb.member, area)

    async def run():
        # party chat: only the sender sees it — bob shares the cell but isn't in a party
        before_b = len(wb.data)
        await server.dispatch(sa, wa, _chat("secret plans", "party"))
        assert b"secret plans" in bytes(wa.data), "sender sees their own party line"
        assert len(wb.data) == before_b, "party chat does NOT reach a non-party roommate"

        # guild chat: same — sender only
        before_b = len(wb.data)
        await server.dispatch(sa, wa, _chat("guild stuff", "guild"))
        assert len(wb.data) == before_b, "guild chat does NOT reach a non-guild roommate"

        # zone chat: everyone in the room gets it (incl. the sender)
        before_b = len(wb.data)
        await server.dispatch(sa, wa, _chat("hello room", "zone"))
        assert len(wb.data) > before_b and b"hello room" in bytes(wb.data), \
            "zone chat reaches everyone in the room"

        print("chat routing OK: party/guild sender-only, zone room-wide")
        print("ALL CHAT TESTS PASSED")

    asyncio.run(run())


if __name__ == "__main__":
    main()
