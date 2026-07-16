"""In-memory party membership. Parties live only as long as their members are online —
there's no persistence (matching AE: a party evaporates when everyone logs off). A party
is a set of uids with an owner; the client renders it from ResponsePartyData (Cmd "pa").

Wire (decompiled Request/ResponseParty*):
  c2s pi [targetName]  -> s2c pi {leaderName, ownerID}     (invite popup on target)
  c2s pa [ownerID]     -> s2c pa {members:[{name,map,accessLevel}], owner, pid}  (to all)
  c2s pd [ownerID]     -> (decline; no state change)
  c2s pl []            -> s2c pr {pid, typ, unm} to the rest; pc {pid} to the leaver
"""
import world

_parties = {}          # pid -> {"owner": uid, "members": [uid, ...]}
_by_uid = {}           # uid -> pid
_next_pid = 1


def party_of(uid):
    pid = _by_uid.get(uid)
    return (pid, _parties[pid]) if pid in _parties else (None, None)


def _new_party(owner_uid):
    global _next_pid
    pid = _next_pid
    _next_pid += 1
    _parties[pid] = {"owner": owner_uid, "members": [owner_uid]}
    _by_uid[owner_uid] = pid
    return pid


def _member_data(uid):
    m = world.find_uid(uid)
    if m is None:
        return None
    access = 0
    try:
        access = int(m.user_obj.get("intAccessLevel", 0) or 0)
    except (TypeError, ValueError, AttributeError):
        access = 0
    # Name MUST be lowercase: the client resolves party members via getPlayer(name), whose
    # playersByName dict is keyed by the lowercase in-world Name (see [[chat-email-guard-gotcha]]).
    # A mixed-case name never matches -> the slot is stuck "out of range" (no live HP), and the
    # self-exclusion in UIPartyFrame (member.name != mainPlayer.Name, also lowercase) fails too.
    return {"name": m.name.lower(), "map": (m.area or "").split("-")[0], "accessLevel": access}


def _broadcast_data(pid):
    """Push the current ResponsePartyData to every online member of the party."""
    p = _parties.get(pid)
    if p is None:
        return
    owner_m = world.find_uid(p["owner"])
    owner_name = owner_m.name.lower() if owner_m else ""   # lowercase to match member names (helmet/leader)
    # Build ONE entry per member, keyed by uid, so we can send each recipient a list that OMITS
    # themselves — the party frame is meant to show the OTHER members (you have your own HP UI).
    # The client also self-excludes, but only when its mainPlayer.Name matches; filtering here is
    # authoritative and recipient-specific, so a name-casing edge can't leave you in your own frame.
    entries = {u: d for u in p["members"] if (d := _member_data(u)) is not None}
    for uid in list(p["members"]):
        members = [d for u, d in entries.items() if u != uid]
        pk = {"Cmd": "pa", "members": members, "owner": owner_name, "pid": pid}
        world.send(world.find_uid(uid), pk)


def invite(inviter, target):
    """inviter (Member) invites target (Member). Creates a party owned by the inviter if they
    aren't already in one, then pushes the invite popup to the target."""
    pid, _ = party_of(inviter.uid)
    if pid is None:
        pid = _new_party(inviter.uid)
    world.send(target, {"Cmd": "pi", "leaderName": inviter.name, "ownerID": inviter.uid})
    return pid


def accept(joiner, owner_uid):
    """joiner (Member) accepts owner_uid's invite. Adds them to that party and resyncs everyone."""
    pid = _by_uid.get(owner_uid)
    p = _parties.get(pid)
    if p is None:                       # inviter created no party / logged off — start one now
        if world.find_uid(owner_uid) is None:
            return None
        pid = _new_party(owner_uid)
        p = _parties[pid]
    # already in a different party? leave it first.
    old_pid, _ = party_of(joiner.uid)
    if old_pid is not None and old_pid != pid:
        leave(joiner)
    if joiner.uid not in p["members"]:
        p["members"].append(joiner.uid)
    _by_uid[joiner.uid] = pid
    _broadcast_data(pid)
    return pid


def leave(member):
    """member leaves their party. Sends pr to the rest; closes the party if it drops below 2."""
    pid, p = party_of(member.uid)
    if p is None:
        return
    p["members"] = [u for u in p["members"] if u != member.uid]
    _by_uid.pop(member.uid, None)
    world.send(member, {"Cmd": "pc", "pid": pid})            # close the leaver's own frame
    if len(p["members"]) < 2:                                # a party of one isn't a party
        for u in list(p["members"]):
            _by_uid.pop(u, None)
            world.send(world.find_uid(u), {"Cmd": "pc", "pid": pid})
        _parties.pop(pid, None)
        return
    # promote a new owner if the owner left
    if p["owner"] == member.uid:
        p["owner"] = p["members"][0]
    for u in p["members"]:
        world.send(world.find_uid(u),
                   {"Cmd": "pr", "pid": pid, "typ": "left", "unm": member.name})
    _broadcast_data(pid)


def member_uids(uid):
    """The uids sharing uid's party (incl. uid), or just [uid] if partyless — for party chat."""
    _, p = party_of(uid)
    return list(p["members"]) if p else [uid]
