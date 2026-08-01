"""Player cosmetic titles — the selectable subtitle shown under the overhead nameplate.

Client feature shipped in build 24358633 (Assets/Scripts/Comm/Messages/*PlayerTitle*.cs,
Assets/Scripts/UI/TitleOption.cs):
  c2s getPlayerTitles              -> s2c {Cmd:getPlayerTitles, Titles:[str,...]}   (owned list)
  c2s savePlayerTitle Params=[str] -> s2c {Cmd:savePlayerTitle, CharID:int, Title:str}

The chosen title rides the normal user object as a top-level "Title" (ComUserData.Title ->
Player.Title in SetUserData), so every client learns it the same way it learns level/class. The
base client does NOT draw it; the InfinityLoader mod renders it as the line BELOW the name (the
guild tag moved ABOVE the name to make room). Selection is stored in the per-char `prefs` blob
(same as PortraitPref) and popped out in game.build_init_player, so no schema change is needed.

Ownership is server-authoritative: savePlayerTitle only accepts a title the character actually
owns (see available_titles), so a crafted Params can't set arbitrary text.
"""
import json

DEV_ACCESS_LEVEL = 100                      # matches game.DEV_ACCESS_LEVEL (staff)

# Titles every character may equip — cosmetic flair, extend freely.
BASE_TITLES = ["Adventurer", "Hero", "Champion", "Slayer", "Legend", "Wanderer"]
# Staff-only titles (access_level >= 100).
DEV_TITLES = ["Architect", "Game Master"]


def _access(char):
    return char["access_level"] if char is not None and "access_level" in char.keys() else 0


def available_titles(char):
    """The list of titles this character may choose from (what getPlayerTitles returns)."""
    titles = list(BASE_TITLES)
    if _access(char) >= DEV_ACCESS_LEVEL:
        titles += DEV_TITLES
    return titles


def is_allowed(char, title):
    """A title is settable if it's empty ('No title' / clear) or one the character owns."""
    return title == "" or title in available_titles(char)


def selected(char):
    """The character's currently-equipped title ('' if none), read from the prefs blob."""
    if char is None or "prefs" not in char.keys() or not char["prefs"]:
        return ""
    try:
        return (json.loads(char["prefs"]) or {}).get("Title", "") or ""
    except (TypeError, ValueError):
        return ""


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
