"""
In-memory multiplayer world: who is in which area, and fan-out to their sockets.

Areas are keyed by map name (instances collapse to one shared room for now).
Each connected player is a Member holding their identity, render data (the
initPlayer 'user' object), live position, and their asyncio writer so we can
push s2c packets to them from anyone's handler.

Broadcasts are fire-and-forget writes (no per-peer await) so one slow client
can't stall another's handler; backpressure on a tiny server is negligible.
"""
import json


class Member:
    __slots__ = ("uid", "name", "user_obj", "area", "frame", "x", "y", "writer")

    def __init__(self, uid, name, user_obj, writer):
        self.uid = uid
        self.name = name
        self.user_obj = user_obj      # full initPlayer 'user' dict (for AreaAdd/uoBranch)
        self.area = None
        self.frame = "Enter"
        self.x = 0.0
        self.y = 0.0
        self.writer = writer


_rooms = {}   # area -> {uid: Member}

# A client that stops reading (crashed, suspended, or malicious) makes asyncio queue our writes
# in its transport buffer unboundedly — a slow memory leak that one bad peer inflicts on the
# whole server. If a socket's queued bytes pass this mark it isn't keeping up; we close it (its
# own handler then runs cleanup_session) and drop it from the room so we stop fanning out to it.
WRITE_HIGH_WATER = 1_048_576   # 1 MiB queued to one client


def _room(area):
    return _rooms.setdefault(area or "infinityportal", {})


def _is_closing(writer):
    """True if the socket is already closing. Tolerant of non-StreamWriter stand-ins (tests)."""
    try:
        return writer.is_closing()
    except AttributeError:
        return False


def _overloaded(writer):
    """True if the client's queued write buffer is over the high-water mark (it's not draining)."""
    try:
        return writer.transport.get_write_buffer_size() > WRITE_HIGH_WATER
    except Exception:
        return False


def _deliver(writer, data):
    """Write one framed message to a socket. Returns False if the client is gone or too slow and
    should be dropped from the room: a closing socket is skipped; a write error or an over-the-
    high-water buffer closes the socket (its handler's cleanup_session then tears the player down)."""
    if _is_closing(writer):
        return False
    try:
        writer.write(data)
    except Exception:
        return False
    if _overloaded(writer):
        try:
            writer.close()
        except Exception:
            pass
        return False
    return True


def join(member, area):
    """Place member in area; return the OTHER members already there."""
    leave(member)                      # ensure not double-listed
    room = _room(area)
    others = [m for m in room.values() if m.uid != member.uid]
    room[member.uid] = member
    member.area = area
    return others


def leave(member):
    """Remove member from their current area; return that area (or None)."""
    if member.area is None:
        return None
    room = _rooms.get(member.area)
    area = member.area
    if room:
        room.pop(member.uid, None)
        if not room:
            _rooms.pop(area, None)
    member.area = None
    return area


def members(area, exclude=None):
    return [m for m in _room(area).values() if m.uid != exclude]


def find_member(name):
    """Find a connected member by name across all rooms (case-insensitive)."""
    if not name:
        return None
    name = name.strip().lower()
    for room in _rooms.values():
        for m in room.values():
            if m.name.lower() == name:
                return m
    return None


def find_uid(uid):
    """Find a connected member by uid across all rooms."""
    for room in _rooms.values():
        m = room.get(uid)
        if m is not None:
            return m
    return None


def entity(member):
    """A CellJoin entity entry representing another player."""
    return {
        "targetString": f"p:{member.uid}",
        "x": member.x, "y": member.y,
        "HP": 100, "State": 1,
        "moveDirection": {}, "moveSpeed": 1.0,
    }


def broadcast(area, obj, exclude=None):
    """Send obj to everyone in area (optionally excluding one uid). Stalled/dead clients are
    dropped from the room (see _deliver) so a bad peer can't back up the whole fan-out."""
    if obj is None:
        return
    data = json.dumps(obj, separators=(",", ":")).encode("utf-8") + b"\x00"
    room = _room(area)
    for m in list(room.values()):
        if m.uid == exclude:
            continue
        if not _deliver(m.writer, data):
            room.pop(m.uid, None)


def send(member, obj):
    """Push obj to a single member's socket (fire-and-forget). Drops the member if it's stalled."""
    if member is None or obj is None:
        return
    data = json.dumps(obj, separators=(",", ":")).encode("utf-8") + b"\x00"
    if not _deliver(member.writer, data) and member.area is not None:
        _rooms.get(member.area, {}).pop(member.uid, None)


def broadcast_all(obj):
    """Send obj to EVERY connected player across all areas (server-wide; e.g. a mod yell)."""
    if obj is None:
        return
    data = json.dumps(obj, separators=(",", ":")).encode("utf-8") + b"\x00"
    for room in list(_rooms.values()):
        for m in list(room.values()):
            if not _deliver(m.writer, data):
                room.pop(m.uid, None)
