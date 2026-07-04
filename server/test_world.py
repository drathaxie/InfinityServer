"""
world.broadcast backpressure: a client that stops draining (its queued write buffer passes the
high-water mark) or is already closing gets dropped from the room, so one stalled peer can't
back up the server's fan-out to everyone else.
"""
import world


class FakeTransport:
    def __init__(self, n):
        self._n = n

    def get_write_buffer_size(self):
        return self._n


class FakeWriter:
    def __init__(self, closing=False, bufsize=0):
        self.transport = FakeTransport(bufsize)
        self._closing = closing
        self.closed = False
        self.data = bytearray()

    def is_closing(self):
        return self._closing

    def write(self, b):
        self.data.extend(b)

    def close(self):
        self.closed = True
        self._closing = True


def main():
    area = "wtest-1"
    world._rooms.pop(area, None)

    good = FakeWriter()
    stalled = FakeWriter(bufsize=world.WRITE_HIGH_WATER + 1)   # not draining
    closing = FakeWriter(closing=True)                        # socket already going away
    mg = world.Member(1, "good", {}, good)
    ms = world.Member(2, "stalled", {}, stalled)
    mc = world.Member(3, "closing", {}, closing)
    for m in (mg, ms, mc):
        world.join(m, area)
    assert len(world.members(area)) == 3

    world.broadcast(area, {"Cmd": "ping"})

    # the healthy client received the message and stays in the room
    assert good.data, "healthy client received the broadcast"
    assert any(m.uid == 1 for m in world.members(area)), "healthy client stays in the room"

    # the over-high-water client is closed and dropped
    assert stalled.closed, "over-high-water client is closed"
    assert not any(m.uid == 2 for m in world.members(area)), "stalled client dropped from room"

    # the already-closing client is skipped (never written to) and dropped
    assert not closing.data, "closing client is not written to"
    assert not any(m.uid == 3 for m in world.members(area)), "closing client dropped from room"

    # a subsequent broadcast still reaches the survivor and doesn't error on the dropped peers
    good.data.clear()
    world.broadcast(area, {"Cmd": "ping2"})
    assert good.data, "survivor still receives broadcasts after the drops"

    print("world backpressure OK: stalled + closing peers dropped, healthy peer unaffected")
    print("ALL WORLD TESTS PASSED")


if __name__ == "__main__":
    main()
