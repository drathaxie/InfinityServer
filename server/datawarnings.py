"""Content problems reported to staff in-client, via the 2026-09-17 client's dataWarnings.

ResponseDataWarnings holds a list of strings and shows them in one message box once the next
cell change finishes — a channel built for exactly the authoring mistakes this server currently
only prints to its own stdout, where whoever is building the map never sees them.

Gated to staff: it is an authoring tool, and a player who wandered into a half-built map should
not get a modal about it.
"""


def _monbranch(area):
    mb = (area or {}).get("monBranch")
    return mb if isinstance(mb, list) else []


def for_area(conn, map_name, area, requested_map=None):
    """Everything wrong with the map being entered, as the strings the client will show.

    Deliberately limited to what is both cheap to check while serving an AreaJoin and actually
    actionable: a placement pointing at content that does not exist. Each warning names the
    monster and the id that is missing so it can be fixed without hunting."""
    warnings = []
    if requested_map and requested_map != map_name:
        warnings.append(
            f"No map data for '{requested_map}' — you are standing in '{map_name}' instead.")

    entries = _monbranch(area)
    if not entries:
        return warnings

    mon_ids, apop_ids = set(), set()
    for e in entries:
        try:
            mon_ids.add(int(e.get("MonID")))
        except (TypeError, ValueError):
            pass
        try:
            aid = int(e.get("apopID"))
        except (TypeError, ValueError):
            aid = -1
        if aid > 0:
            apop_ids.add(aid)

    known_mons, known_apops = set(), set()
    if mon_ids:
        known_mons = {int(r["mon_id"]) for r in conn.execute(
            "SELECT mon_id FROM monsters WHERE mon_id IN (%s)"
            % ",".join("?" * len(mon_ids)), tuple(sorted(mon_ids))).fetchall()}
    if apop_ids:
        known_apops = {int(r["apop_id"]) for r in conn.execute(
            "SELECT apop_id FROM apops WHERE apop_id IN (%s)"
            % ",".join("?" * len(apop_ids)), tuple(sorted(apop_ids))).fetchall()}

    for e in entries:
        name = e.get("strMonName") or e.get("Name") or "?"
        pad = e.get("MonMapID")
        try:
            mid = int(e.get("MonID"))
        except (TypeError, ValueError):
            warnings.append(f"'{name}' (pad {pad}) has no MonID.")
            continue
        if mid not in known_mons:
            warnings.append(f"'{name}' (pad {pad}) points at monster {mid}, which has no "
                            f"catalog row — it will not render.")
        try:
            aid = int(e.get("apopID"))
        except (TypeError, ValueError):
            aid = -1
        if aid > 0 and aid not in known_apops:
            warnings.append(f"'{name}' (pad {pad}) opens apop {aid}, which does not exist — "
                            f"talking to it will do nothing.")
    return warnings


def packet(warnings):
    """ResponseDataWarnings, or None when there is nothing to report (the client clears any
    pending warnings on every one it receives, so an empty one is not worth sending)."""
    if not warnings:
        return None
    return {"Cmd": "dataWarnings", "warnings": list(warnings)}
