"""Server-authored monster movement: the monWalk / monLeash contract the 2026-09-17 client added.

Until this build a monster stood on its spawn point forever — the client had no way to be told
one had moved. The new build added two s2c packets and drives them through the monster's own
walk animation:

  monWalk  {monMapID, x, y, speed}  -> Monster.animation.walk.walkTo(pos, speed, serverAuthored)
  monLeash {monMapID}               -> SetLeashState(true) + moveBack() (walks home by itself)

Both are purely server-driven: there is no c2s counterpart, and the client never decides on its
own that a monster should move. So the rules live here.

What this does NOT do is idle wandering. AE's monsters wander their pad when nothing is
happening, but that needs the authored pad extents (map_pads) to stay inside, and a monster that
wanders is a monster whose position every client must agree on. Chasing and leashing are the
half that combat already has the state for, so that is the half implemented here.

Positions are kept per (area, target string) because each room is its own instance — two players
in different copies of battleon must not move each other's monsters.
"""
import math
import time

# One monWalk per monster per this many seconds. The client tweens between points, so a lower
# rate looks smooth; the packet exists to correct the destination, not to stream a path.
CHASE_INTERVAL = 0.75
# How close the monster walks to its target. Melee range, so it stops ON the player rather than
# inside them, and so a jittering player does not drag it back and forth.
STOP_RANGE = 2.5
# A chase may not drag a monster further than this from its spawn: past it the monster gives up
# and leashes, which is what stops a player kiting a boss across the map.
LEASH_RANGE = 20.0
# Matches the walk speed the client uses for its own monster movement.
WALK_SPEED = 2.5
# Below this the monster is home; moving it again would just resend the same point.
ARRIVED_EPSILON = 0.35

_spawn = {}      # (area, mon_ts) -> {"x": float, "y": float, "no_move": bool}
_pos = {}        # (area, mon_ts) -> (x, y)   current position, spawn until it moves
_last_walk = {}  # (area, mon_ts) -> time.time() of the last monWalk


def register_area(area, monbranch):
    """Learn every monster's spawn point and NoMove flag for one room, from the same monBranch
    the AreaJoin serves. Re-joining a room re-registers it, which also resets any displacement
    left over from the previous occupants."""
    if not isinstance(monbranch, list):
        return
    forget_area(area)
    for e in monbranch:
        try:
            mmid = int(e.get("MonMapID"))
        except (TypeError, ValueError):
            continue
        ts = f"m:{mmid}"
        try:
            x, y = float(e.get("x") or 0.0), float(e.get("y") or 0.0)
        except (TypeError, ValueError):
            x, y = 0.0, 0.0
        _spawn[(area, ts)] = {"x": x, "y": y, "no_move": bool(e.get("NoMove", False))}
        _pos[(area, ts)] = (x, y)


def forget_area(area):
    """Drop a room's movement state (the room emptied, or is being rebuilt)."""
    for key in [k for k in _spawn if k[0] == area]:
        _spawn.pop(key, None)
        _pos.pop(key, None)
        _last_walk.pop(key, None)


def position(area, mon_ts):
    """Where this monster currently stands, or None if the room never registered it."""
    return _pos.get((area, mon_ts))


def spawn_point(area, mon_ts):
    s = _spawn.get((area, mon_ts))
    return (s["x"], s["y"]) if s else None


def can_move(area, mon_ts):
    """NoMove monsters (AE's per-placement flag) are scenery and never walk."""
    s = _spawn.get((area, mon_ts))
    return bool(s) and not s["no_move"]


def displaced(area, mon_ts):
    """True when the monster is standing away from its spawn point."""
    here, home = _pos.get((area, mon_ts)), spawn_point(area, mon_ts)
    if here is None or home is None:
        return False
    return math.dist(here, home) > ARRIVED_EPSILON


def chase(area, mon_ts, px, py, now=None):
    """The monWalk that moves this monster toward (px, py), or None to stay put.

    None means one of: the monster is scenery, it is already in range, its last walk was too
    recent, or the target is past its leash. The caller broadcasts whatever comes back."""
    key = (area, mon_ts)
    if not can_move(area, mon_ts):
        return None
    here, home = _pos.get(key), spawn_point(area, mon_ts)
    if here is None or home is None:
        return None
    # time.time(), matching the AI loop that supplies `now` — the two must not be mixed, since
    # the rate limit compares them directly.
    now = time.time() if now is None else now
    if now - _last_walk.get(key, 0.0) < CHASE_INTERVAL:
        return None

    dist = math.dist(here, (px, py))
    if dist <= STOP_RANGE:
        return None
    # Walk to STOP_RANGE short of the target rather than on top of it.
    ratio = (dist - STOP_RANGE) / dist
    dest = (here[0] + (px - here[0]) * ratio, here[1] + (py - here[1]) * ratio)
    # Refuse to be kited: a destination past the leash means give up instead of following.
    if math.dist(dest, home) > LEASH_RANGE:
        return None

    _pos[key] = dest
    _last_walk[key] = now
    return walk_packet(mon_ts, dest[0], dest[1])


def leash(area, mon_ts):
    """The monLeash that sends a displaced monster home, or None if it is already there.

    The client walks it back itself (moveBack), so the server just books it as home again."""
    key = (area, mon_ts)
    home = spawn_point(area, mon_ts)
    if home is None or not displaced(area, mon_ts):
        return None
    _pos[key] = home
    _last_walk.pop(key, None)
    return leash_packet(mon_ts)


def leash_idle(engaged):
    """Every monster that wandered off but is no longer engaged -> (area, monLeash packet).

    Reconciling against the live engagement set each tick catches every way aggro can end —
    the player left, died, killed it, or simply timed out — without each of those paths having
    to remember to leash."""
    out = []
    for (area, mon_ts) in [k for k in _pos]:
        if (area, mon_ts) in engaged:
            continue
        pkt = leash(area, mon_ts)
        if pkt is not None:
            out.append((area, pkt))
    return out


def _mon_map_id(mon_ts):
    try:
        return int(str(mon_ts).split(":", 1)[1])
    except (IndexError, ValueError):
        return 0


def walk_packet(mon_ts, x, y, speed=WALK_SPEED):
    """ResponseMonsterWalk. x/y are the monster's FOOTPRINT position in the frame's local
    space — the client offsets by the sprite's own footprint before it walks."""
    return {"Cmd": "monWalk", "monMapID": _mon_map_id(mon_ts),
            "x": round(float(x), 3), "y": round(float(y), 3), "speed": float(speed)}


def leash_packet(mon_ts):
    """ResponseMonsterLeash."""
    return {"Cmd": "monLeash", "monMapID": _mon_map_id(mon_ts)}
