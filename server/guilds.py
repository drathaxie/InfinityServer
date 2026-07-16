"""Guilds — persistent (guilds table + characters.guild_id/guild_rank). The client's
Guild object is {ID, Name, maxMembers, dateUpdated, MOTD, Users:{idStr:GuildPlayer}};
GuildPlayer = {Rank, ID, userName, Level, Server}. Rank: 0 member, 1 officer, 2 leader.

Wire (decompiled Request/ResponseGuild*):
  c2s gc  [name]      -> s2c newGuild {guild}                     (to creator)
  c2s gi  [playerName]-> s2c guildinvite {guildID, owner, gName}  (popup on target)
  c2s gia [ownerName] -> s2c newGuild {guild} (joiner) + gAddMem {player} (existing members)
  c2s gid [ownerName] -> (decline; no state)
  c2s gp  [userID]    -> s2c guildPromote {rank, userID}          (broadcast)
  c2s gd  [userID]    -> s2c guildDemote  {rank, userID}          (broadcast)
  c2s gk  [userID]    -> s2c guildRemove  {UserID}                (broadcast)
  c2s gmotd [msg]     -> s2c gMOTD {MOTD}                         (broadcast)
"""
import json

import world

MAX_MEMBERS = 800
RANK_MEMBER, RANK_OFFICER, RANK_LEADER = 0, 1, 2

# The guild hall is modelled on the house system: a decoratable shared map keyed per guild.
# DEFAULT_HALL_MAP is a plain area we serve as the hall canvas (the "clubhouse" fits a guild);
# per-guild override lives in guilds.hall_map, global override in kv 'guildhall_map'.
DEFAULT_HALL_MAP = "clubhouse"


def _guild_player(row):
    # Server is BOTH the location text and the online flag: the guild panel lights the green dot
    # on `Server != "Offline"`, so an offline member MUST send the literal "Offline" (an empty
    # string reads as online). Online -> the map they're on. [[new-client-social-commands]]
    online = world.find_member(row["name"])
    server = ((online.area or "").split("-")[0] or "In Game") if online else "Offline"
    return {"Rank": row["guild_rank"], "ID": row["id"], "userName": row["name"],
            "Level": row["level"], "Server": server}


def members(conn, guild_id):
    return conn.execute(
        "SELECT id, name, level, guild_rank FROM characters WHERE guild_id=? ORDER BY guild_rank DESC, id",
        (guild_id,)).fetchall()


def guild_object(conn, guild_id):
    """Full Guild dict for initPlayer.guild / newGuild (None if the char has no guild)."""
    if not guild_id:
        return None
    g = conn.execute("SELECT id, name, motd FROM guilds WHERE id=?", (guild_id,)).fetchone()
    if g is None:
        return None
    users = {str(r["id"]): _guild_player(r) for r in members(conn, guild_id)}
    return {"ID": g["id"], "Name": g["name"], "maxMembers": MAX_MEMBERS,
            "dateUpdated": "2000-01-01T00:00:00", "MOTD": g["motd"], "Users": users}


def online_member_uids(conn, char_id):
    """uids of online guildmates (incl. char_id) — for guild chat. Solo/guildless => [uid]."""
    row = conn.execute("SELECT guild_id FROM characters WHERE id=?", (char_id,)).fetchone()
    gid = row["guild_id"] if row else 0
    self_uid = 1_000_000 + int(char_id)
    if not gid:
        return [self_uid]
    uids = []
    for r in members(conn, gid):
        m = world.find_member(r["name"])
        if m is not None:
            uids.append(m.uid)
    return uids or [self_uid]


def broadcast(conn, guild_id, pk, exclude_char_id=None):
    """Send pk to every ONLINE member of the guild (optionally skipping one char id)."""
    for r in members(conn, guild_id):
        if exclude_char_id is not None and r["id"] == exclude_char_id:
            continue
        m = world.find_member(r["name"])
        if m is not None:
            world.send(m, pk)


def guild_by_name(conn, name):
    """The guild row whose name matches (case-insensitive), or None."""
    if not name:
        return None
    return conn.execute("SELECT * FROM guilds WHERE lower(name)=?",
                        (str(name).strip().lower(),)).fetchone()


def leader_row(conn, guild_id):
    """The leader character row of a guild (rank 2; falls back to the `owner` id), or None."""
    row = conn.execute(
        "SELECT * FROM characters WHERE guild_id=? AND guild_rank=? ORDER BY id LIMIT 1",
        (guild_id, RANK_LEADER)).fetchone()
    if row is not None:
        return row
    g = conn.execute("SELECT owner FROM guilds WHERE id=?", (guild_id,)).fetchone()
    if g is None:
        return None
    return conn.execute("SELECT * FROM characters WHERE id=?", (g["owner"],)).fetchone()


# --- guild hall layout (mirrors game._house_layout / house_save, but keyed by guild) ----------
def hall_layout(conn, guild_id):
    """The stored {frame:[PlacedHouseItem]} dict the leader has decorated ({} if none)."""
    row = conn.execute("SELECT hall_data FROM guilds WHERE id=?", (guild_id,)).fetchone()
    try:
        layout = json.loads(row["hall_data"]) if row and row["hall_data"] else {}
    except (TypeError, ValueError):
        layout = {}
    return layout if isinstance(layout, dict) else {}


def save_hall_layout(conn, guild_id, frame, data):
    """Persist ONE frame's placement list into the hall's {frame:[...]} dict (frame '*' clears
    the whole hall). Same merge semantics as game.house_save. Returns True on success."""
    frame = frame or ""
    if frame == "*":
        layout = {}
    else:
        layout = hall_layout(conn, guild_id)
        try:
            placed = json.loads(data) if data else []
        except (TypeError, ValueError):
            return False
        if placed:
            layout[frame] = placed
        else:
            layout.pop(frame, None)
    conn.execute("UPDATE guilds SET hall_data=? WHERE id=?",
                 (json.dumps(layout, separators=(",", ":")), guild_id))
    conn.commit()
    return True


def hall_map(conn, guild_id):
    """The map name a guild's hall opens: per-guild hall_map > kv 'guildhall_map' > DEFAULT_HALL_MAP."""
    import db
    g = conn.execute("SELECT hall_map FROM guilds WHERE id=?", (guild_id,)).fetchone()
    if g is not None and (g["hall_map"] or "").strip():
        return g["hall_map"].strip()
    return (db.kv_get(conn, "guildhall_map") or "").strip() or DEFAULT_HALL_MAP


def leave(conn, char):
    """`char` leaves their guild. Removes them; promotes the highest-ranked remaining member to
    leader if the leader left; disbands (deletes) the guild if nobody remains. Returns a dict
    {gid, left_id, disbanded, new_leader_id} for the handler to broadcast, or None if guildless."""
    gid = char["guild_id"] if "guild_id" in char.keys() else 0
    if not gid:
        return None
    was_leader = int(char["guild_rank"] or 0) >= RANK_LEADER
    conn.execute("UPDATE characters SET guild_id=0, guild_rank=0 WHERE id=?", (char["id"],))
    remaining = members(conn, gid)
    if not remaining:
        conn.execute("DELETE FROM guilds WHERE id=?", (gid,))
        conn.commit()
        return {"gid": gid, "left_id": char["id"], "disbanded": True, "new_leader_id": None}
    new_leader_id = None
    if was_leader:
        # members() is ordered rank DESC, id — the top remaining member inherits leadership.
        new_leader_id = remaining[0]["id"]
        conn.execute("UPDATE characters SET guild_rank=? WHERE id=?", (RANK_LEADER, new_leader_id))
        conn.execute("UPDATE guilds SET owner=? WHERE id=?", (new_leader_id, gid))
    conn.commit()
    return {"gid": gid, "left_id": char["id"], "disbanded": False, "new_leader_id": new_leader_id}


def create(conn, char, name):
    """Create a guild owned by char (becomes leader). Returns guild_id, or None if the name is
    taken or the char is already in a guild."""
    if char["guild_id"]:
        return None
    exists = conn.execute("SELECT 1 FROM guilds WHERE lower(name)=?", (name.lower(),)).fetchone()
    if exists is not None:
        return None
    gid = conn.execute("INSERT INTO guilds(name, motd, owner) VALUES(?,?,?) RETURNING id",
                       (name, "", char["id"])).fetchone()["id"]
    conn.execute("UPDATE characters SET guild_id=?, guild_rank=? WHERE id=?",
                 (gid, RANK_LEADER, char["id"]))
    return gid
