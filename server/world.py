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


def _room(area):
    return _rooms.setdefault(area or "infinityportal", {})


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
    """Send obj to everyone in area (optionally excluding one uid)."""
    if obj is None:
        return
    data = json.dumps(obj, separators=(",", ":")).encode("utf-8") + b"\x00"
    for m in list(_room(area).values()):
        if m.uid == exclude:
            continue
        try:
            m.writer.write(data)
        except Exception:
            pass


def send(member, obj):
    """Push obj to a single member's socket (fire-and-forget)."""
    if member is None or obj is None:
        return
    try:
        member.writer.write(json.dumps(obj, separators=(",", ":")).encode("utf-8") + b"\x00")
    except Exception:
        pass


def broadcast_all(obj):
    """Send obj to EVERY connected player across all areas (server-wide; e.g. a mod yell)."""
    if obj is None:
        return
    data = json.dumps(obj, separators=(",", ":")).encode("utf-8") + b"\x00"
    for room in list(_rooms.values()):
        for m in list(room.values()):
            try:
                m.writer.write(data)
            except Exception:
                pass


def population():
    return {a: len(r) for a, r in _rooms.items() if r}
