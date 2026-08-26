"""Custom player titles delivered through AE's native title pipeline.

The current live client sends ``getPlayerTitles`` after ``initPlayer`` and equips a choice with
``savePlayerTitle``.  We keep that exact message contract and the client's native ``Player.Title``
rendering, while this module remains the server-authoritative catalog of *our* custom titles.
"""
import json

DEV_ACCESS_LEVEL = 100                      # matches game.DEV_ACCESS_LEVEL (staff)

# Custom title inventory.  The native client is only the picker/renderer; it does not own this
# list.  Extend these lists (or add entitlement-backed groups) without changing the wire protocol.
BASE_TITLES = ["Adventurer", "Hero", "Champion", "Slayer", "Legend", "Wanderer"]
DEV_TITLES = ["Architect", "Game Master"]


def _access(char):
    return char["access_level"] if char is not None and "access_level" in char.keys() else 0


def available_titles(char):
    """Custom titles this character owns, returned as the native picker list."""
    titles = list(BASE_TITLES)
    if _access(char) >= DEV_ACCESS_LEVEL:
        titles += DEV_TITLES
    return titles


def is_allowed(char, title):
    """A title is settable if it's empty ('No title' / clear) or one the character owns."""
    return title == "" or title in available_titles(char)


def selected(char):
    """The character's valid equipped custom title (``''`` if none)."""
    if char is None or "prefs" not in char.keys() or not char["prefs"]:
        return ""
    try:
        title = (json.loads(char["prefs"]) or {}).get("Title", "") or ""
    except (TypeError, ValueError):
        return ""
    return title if title in available_titles(char) else ""


def set_selected(conn, char, title):
    """Persist the chosen title into the char's prefs blob (game.build_init_player pops it back
    out as the top-level user "Title"). Pass '' to clear. Caller must have validated is_allowed."""
    prefs = {}
    if "prefs" in char.keys() and char["prefs"]:
        try:
            prefs = json.loads(char["prefs"]) or {}
        except (TypeError, ValueError):
            prefs = {}
    if title:
        prefs["Title"] = title
    else:
        prefs.pop("Title", None)
    conn.execute("UPDATE characters SET prefs=? WHERE id=?", (json.dumps(prefs), char["id"]))
    conn.commit()
