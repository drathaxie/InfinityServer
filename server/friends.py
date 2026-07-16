"""Friends list — mutual, persistent (friends table). The client's FriendObject is
{Level, ID, Name, Server}; ID is the friend's character id (echoed back on deleteFriend).
Server shows their map when online, the literal "Offline" when offline (the friends panel lights
the green dot on `Server != "Offline"`, so an empty string wrongly reads as online).

Wire (decompiled Request/ResponseFriend*):
  c2s requestFriend [tName]  -> s2c requestFriend {unm}       (accept/decline popup on target)
  c2s addFriend    [tName]   -> s2c addFriend {friend:FriendObject}  (to BOTH, once linked)
  c2s declineFriend[tName]   -> (no state change)
  c2s deleteFriend [friendID]-> s2c deleteFriend {ID}         (to self; and to the ex-friend)
"""
import world


def _char_by_name(conn, name):
    if not name:
        return None
    return conn.execute("SELECT id, name, level FROM characters WHERE lower(name)=?",
                        (str(name).strip().lower(),)).fetchone()


def friend_object(conn, friend_id):
    """Build a FriendObject for a friend character id (None if the char is gone)."""
    row = conn.execute("SELECT id, name, level FROM characters WHERE id=?",
                       (friend_id,)).fetchone()
    if row is None:
        return None
    online = world.find_member(row["name"])
    server = ((online.area or "").split("-")[0] or "In Game") if online else "Offline"
    return {"Level": row["level"], "ID": row["id"], "Name": row["name"], "Server": server}


def friend_list(conn, char_id):
    """All FriendObjects for a character's friends — initPlayer.friends."""
    ids = [r["friend_id"] for r in conn.execute(
        "SELECT friend_id FROM friends WHERE char_id=? ORDER BY friend_id", (char_id,)).fetchall()]
    return [fo for fo in (friend_object(conn, fid) for fid in ids) if fo is not None]


def are_friends(conn, a_id, b_id):
    return conn.execute("SELECT 1 FROM friends WHERE char_id=? AND friend_id=?",
                        (a_id, b_id)).fetchone() is not None


def link(conn, a_id, b_id):
    """Create the mutual friendship (idempotent)."""
    if a_id == b_id:
        return
    conn.execute("INSERT INTO friends(char_id, friend_id) VALUES(?,?) "
                 "ON CONFLICT(char_id, friend_id) DO NOTHING", (a_id, b_id))
    conn.execute("INSERT INTO friends(char_id, friend_id) VALUES(?,?) "
                 "ON CONFLICT(char_id, friend_id) DO NOTHING", (b_id, a_id))


def unlink(conn, a_id, b_id):
    conn.execute("DELETE FROM friends WHERE char_id=? AND friend_id=?", (a_id, b_id))
    conn.execute("DELETE FROM friends WHERE char_id=? AND friend_id=?", (b_id, a_id))
