"""
Skill Forge — the in-client class/skill node-graph editor, self-hosted.

The client's SkillForge UI (decompiled: SkillForge.cs + RuntimeNodeEditor/*)
opens by sending c2s `sfInit` (no params). We reply with `sfInit` carrying:

  - the node *palette* (headers/nodes/helpers/conditionals/activators), each a
    List<NodeLayout> the editor renders its sidebar + graphs from. AE never
    answered sfInit in any capture, so there is nothing to copy — this palette
    is ours (data/skill_palette.json), modelled on the runtime Node* executors;
    server/combat.py is the matching interpreter.
  - `classes`  = { ClassName: { ID, Bundle, Skills: { slot: skillID } } }
  - `skills`   = { skillID: <Skill> }, the shared library (CharacterClass.AllSkills).
    The client deserializes each <Skill> via its [JsonConstructor], so the keys
    here must match those constructor params (case-insensitive): id, action,
    name, description, icon, slot, data, forgedata, autohRange, autovRange, mana.

Mutations (sfNew/sfNewLib/sfEdit/sfSave/sfClone/sfLink/sfDel) persist to the
same tables and are handled in their own functions (Stage 2).
"""
import json
import pathlib

DATA = pathlib.Path(__file__).resolve().parent.parent / "data"
PALETTE_FILE = DATA / "skill_palette.json"


def load_palette():
    """The five node categories. Missing/empty categories are fine — the editor
    just shows fewer sidebar entries — so a partial palette still opens."""
    try:
        p = json.loads(PALETTE_FILE.read_text(encoding="utf-8"))
    except Exception:
        p = {}
    return {cat: (p.get(cat) or {}) for cat in
            ("headers", "nodes", "helpers", "conditionals", "activators")}


def _parse(s, fallback):
    try:
        v = json.loads(s)
        return v if v is not None else fallback
    except Exception:
        return fallback


def skill_object(row):
    """One skills-table row -> the <Skill> JSON the client reconstructs. Keys are
    chosen to match Skill's [JsonConstructor] parameter names (Newtonsoft matches
    case-insensitively); `mana` in particular must be lowercase, not RegularMana."""
    return {
        "ID": row["skill_id"],
        "Action": row["action"],
        "Name": row["name"] or "",
        "Description": row["description"] or "",
        "Icon": row["icon"] or "",
        "Slot": row["slot"],
        "Data": _parse(row["data"], [{}, {}]),
        "ForgeData": _parse(row["forge_data"], [{}, {}]),
        "AutoHRange": row["auto_h_range"],
        "AutoVRange": row["auto_v_range"],
        "AutoHoldAtRange": bool(row["auto_hold_at_range"]),
        "mana": row["mana"],
    }


def all_skills(conn):
    return {str(r["skill_id"]): skill_object(r)
            for r in conn.execute("SELECT * FROM skills ORDER BY skill_id")}


def classes_object(conn):
    """{ ClassName: { ID, Bundle, Skills: { "slot": skillID } } } — the editor
    keys the class list by name and auto-selects the one whose ID == the player's
    Info.ClassID."""
    out = {}
    for c in conn.execute("SELECT * FROM classes ORDER BY class_id"):
        skills = {str(s["slot"]): s["skill_id"] for s in conn.execute(
            "SELECT slot, skill_id FROM class_skills WHERE class_id=? ORDER BY slot",
            (c["class_id"],))}
        out[c["name"]] = {"ID": str(c["class_id"]),
                          "Bundle": c["bundle"] or "",
                          "Skills": skills}
    return out


# The class the client currently has equipped == the one we send in sEAct at login
# (the Dragonslayer sample, class 1932, which also matches the initPlayer template).
# Combat resolves a pressed slot through THIS class's authored skills.
EQUIPPED_CLASS_ID = 1932


def _graph_particles(data_json):
    """Particle/aura-VFX names referenced by a skill graph (for sEAct's particleList,
    which the client preloads). AuraVFX assets are loaded as <VFX>_Appear / <VFX>_Exit
    (see NodeAuraVFX.Execute), so we list both variants."""
    out = []
    data = _parse(data_json, [{}, {}])
    nodes = data[1] if isinstance(data, list) and len(data) > 1 and isinstance(data[1], dict) else {}
    for props in nodes.values():
        if not isinstance(props, dict):
            continue
        if props.get("Name") == "Particle" and props.get("Particle"):
            out.append(props["Particle"])
        elif props.get("Name") == "SpellAnimation":
            # NodeSpellAnimation queues both assets from the equipped class bundle.
            # Preloading them in sEAct prevents the first cast racing the async load.
            for key in ("SpellGraphic", "SpellImpact"):
                if props.get(key):
                    out.append(props[key])
        elif props.get("Name") == "AuraVFX" and props.get("VFX"):
            out.append(props["VFX"] + "_Appear")
            out.append(props["VFX"] + "_Exit")
    return out


def build_seact(conn, class_id):
    """The s2c sEAct (ResponseEquipActions) for a class, from the DB — so the HUD
    skill bar shows our authored skill names/icons/descriptions. Mirrors the
    captured shape: skillList{slot:{id,act,nam,icon,desc,autoHRange,autoVRange[,regMana]}}
    + particleList (class particles to preload, gathered from the skill graphs)."""
    skill_list = {}
    particles = []
    for r in conn.execute(
            "SELECT cs.slot AS cslot, s.skill_id, s.action, s.name, s.icon, s.description, "
            "s.auto_h_range, s.auto_v_range, s.auto_hold_at_range, s.mana, s.data "
            "FROM class_skills cs JOIN skills s ON s.skill_id=cs.skill_id "
            "WHERE cs.class_id=? ORDER BY cs.slot", (int(class_id),)):
        entry = {"id": r["skill_id"], "act": r["action"], "nam": r["name"] or "",
                 "icon": r["icon"] or "", "desc": r["description"] or "",
                 "autoHRange": r["auto_h_range"], "autoVRange": r["auto_v_range"],
                 "autoHoldAtRange": bool(r["auto_hold_at_range"])}
        if r["mana"]:
            entry["regMana"] = r["mana"]
        skill_list[str(r["cslot"])] = entry
        for p in _graph_particles(r["data"]):
            if p not in particles:
                particles.append(p)
    return {"Cmd": "sEAct", "skillList": skill_list, "particleList": particles}


def _armor_item_map(conn):
    """class-armor item_id -> class_id. The authoritative source is the class rig's
    eqp.Class.ID (the real class-item id the client equips, e.g. 15651 = Healer, 582 =
    Dragonslayer), so equipping a class armor switches the equipped class + skills."""
    out = {}
    for c in conn.execute("SELECT class_id, rig FROM classes WHERE rig IS NOT NULL"):
        try:
            iid = (json.loads(c["rig"]) or {}).get("ID")
        except Exception:
            iid = None
        if iid is not None:
            out[int(iid)] = c["class_id"]
    return out


def class_for_armor_item(conn, item_id):
    try:
        return _armor_item_map(conn).get(int(item_id))
    except (TypeError, ValueError):
        return None


# Per-class resource bar (s2c updateClass / ResponseClass). DS = Determination (white bar
# that turns orange at the 50 Threshold); every other captured class = a mana/rage pool
# (blue, no threshold). The blue model is also the fallback for a class with no authored
# resource. Both confirmed in capture: DS={16777215,_,100,50,16745728}, others={255,100,100,-1,-1}.
_RESOURCE_DEFAULT = {"model": "mana", "ResourceColor": 255, "MaxRP": 100,
                     "Threshold": -1, "ThresholdColor": -1}


def resource_for_class(conn, class_id):
    """The class's resource bar model (parsed), merged over the mana/blue default."""
    row = conn.execute("SELECT resource FROM classes WHERE class_id=?",
                       (int(class_id),)).fetchone()
    if row and row["resource"]:
        try:
            r = json.loads(row["resource"])
            if isinstance(r, dict):
                return {**_RESOURCE_DEFAULT, **r}
        except Exception:
            pass
    return dict(_RESOURCE_DEFAULT)


def build_updateclass(conn, class_id, uid, rp=None):
    """s2c updateClass (ResponseClass) carrying the class's real resource bar — colors,
    MaxRP, and the Determined threshold. `rp` overrides the starting fill; by default a
    mana class starts full and a Determination class starts empty."""
    res = resource_for_class(conn, class_id)
    if rp is None:
        rp = res["MaxRP"] if res["model"] == "mana" else 0
    return {"Cmd": "updateClass", "uid": uid,
            "ResourceColor": res["ResourceColor"], "RP": rp, "MaxRP": res["MaxRP"],
            "Threshold": res["Threshold"], "ThresholdColor": res["ThresholdColor"]}


def class_mana_costs(conn, class_id):
    """{skill_id: mana_cost} for a class's skills. The DB stores `mana` = regMana (negative
    for a cost; RegularMana = regMana * -1, Skill.cs:64), so cost = max(0, -mana)."""
    out = {}
    for r in conn.execute(
            "SELECT s.skill_id, s.mana FROM class_skills cs "
            "JOIN skills s ON s.skill_id=cs.skill_id WHERE cs.class_id=?", (int(class_id),)):
        out[int(r["skill_id"])] = max(0, -int(r["mana"] or 0))
    return out


def skill_for_slot(conn, class_id, slot):
    """The (parsed) authored skill graph at a class slot, for combat to walk.
    None if the slot is empty."""
    r = conn.execute(
        "SELECT s.* FROM class_skills cs JOIN skills s ON s.skill_id=cs.skill_id "
        "WHERE cs.class_id=? AND cs.slot=?", (int(class_id), int(slot))).fetchone()
    if r is None:
        return None
    return {"skill_id": r["skill_id"], "name": r["name"],
            "data": _parse(r["data"], [{}, {}]),
            "forge": _parse(r["forge_data"], [{}, {}])}


def linear_graph(nodes):
    """Build (data, forge) JArrays for a straight-line skill graph, the same shape the
    in-client editor's Export produces. `nodes` = [(id, props), ...]; nodes[0] is the
    header (OnRequest). Lets us seed real skills from their captured node sequences."""
    header_id, header_props = nodes[0]
    data = [{header_id: header_props}, {nid: props for nid, props in nodes[1:]}]

    def chain(i):
        if i >= len(nodes):
            return None
        node = {"id": nodes[i][0]}
        nxt = chain(i + 1)
        if nxt is not None:
            node["Next"] = nxt
        return node

    tree = {header_id: ({"Next": chain(1)} if len(nodes) > 1 else {})}
    pos = {nid: {"X": float(i * 260 - 1000), "Y": 0.0} for i, (nid, _) in enumerate(nodes)}
    return data, [pos, tree]


def _norm_graph(s):
    """Coerce a node-graph string into a valid 2-element JArray string. The editor
    sends "[]" for a brand-new (never-opened) graph, but LoadData does
    data[0].Properties() and would NRE on an empty array — so empty/short graphs
    become [{},{}] (header dict, node dict)."""
    v = _parse(s if isinstance(s, str) else json.dumps(s), None)
    if not isinstance(v, list) or len(v) < 2:
        v = [{}, {}]
    return json.dumps(v, separators=(",", ":"))


def _next_skill_id(conn):
    m = conn.execute("SELECT MAX(skill_id) AS m FROM skills").fetchone()["m"] or 0
    return max(m, 9999) + 1          # authored skills start above captured ids


def _next_free_slot(conn, class_id):
    used = {r["slot"] for r in conn.execute(
        "SELECT slot FROM class_skills WHERE class_id=?", (class_id,))}
    s = 0
    while s in used:
        s += 1
    return s


def _skill_row(conn, skill_id):
    return conn.execute("SELECT * FROM skills WHERE skill_id=?", (skill_id,)).fetchone()


def _write_skill(conn, sid, action, name, desc, icon, slot, data, forge_):
    """Upsert a library skill. On update, auto_h/v_range and mana are preserved
    (the editor doesn't send them) by simply not naming them in the UPDATE set."""
    conn.execute(
        "INSERT INTO skills(skill_id, action, name, description, icon, slot, data, forge_data) "
        "VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(skill_id) DO UPDATE SET "
        "action=excluded.action, name=excluded.name, description=excluded.description, "
        "icon=excluded.icon, slot=excluded.slot, data=excluded.data, forge_data=excluded.forge_data",
        (sid, int(action or 0), name, desc, icon, int(slot or 0),
         _norm_graph(data), _norm_graph(forge_)))


def _set_class_slot(conn, class_id, slot, skill_id):
    conn.execute(
        "INSERT INTO class_skills(class_id, slot, skill_id) VALUES(?,?,?) "
        "ON CONFLICT(class_id, slot) DO UPDATE SET skill_id=excluded.skill_id",
        (class_id, int(slot), int(skill_id)))


# --- mutation handlers: each takes the c2s Params list, persists, returns the
#     s2c reply dict (or an sfError). Param order is from the Request* classes. ---

def _err(msg):
    return {"Cmd": "sfError", "msg": msg}


def sf_new(conn, p):
    """sfNew[classID,action,name,desc,icon,slot,data,forge] — new skill on a class."""
    class_id, action, name, desc, icon, slot, data, forge_ = (p + [None] * 8)[:8]
    sid = _next_skill_id(conn)
    _write_skill(conn, sid, action, name, desc, icon, slot, data, forge_)
    _set_class_slot(conn, int(class_id), int(slot or 0), sid)
    conn.commit()
    return {"Cmd": "sfNew", "Name": name, "Slot": int(slot or 0), "Skill": sid,
            "Data": skill_object(_skill_row(conn, sid))}


def sf_new_lib(conn, p):
    """sfNewLib[action,name,desc,icon,data,forge] — new library-only skill."""
    action, name, desc, icon, data, forge_ = (p + [None] * 6)[:6]
    sid = _next_skill_id(conn)
    _write_skill(conn, sid, action, name, desc, icon, 0, data, forge_)
    conn.commit()
    return {"Cmd": "sfNewLib", "Skill": sid,
            "Data": skill_object(_skill_row(conn, sid))}


def sf_edit(conn, p):
    """sfEdit[skillID,action,name,desc,icon,data,forge] — edit a library skill."""
    skill_id, action, name, desc, icon, data, forge_ = (p + [None] * 7)[:7]
    sid = int(skill_id)
    if _skill_row(conn, sid) is None:
        return _err(f"Skill {sid} not found.")
    row = _skill_row(conn, sid)
    _write_skill(conn, sid, action, name, desc, icon, row["slot"], data, forge_)
    conn.commit()
    return {"Cmd": "sfEdit", "Skill": sid,
            "Data": skill_object(_skill_row(conn, sid))}


def sf_save(conn, p):
    """sfSave[classID,prevSlot,slot,skillID,action,name,desc,icon,data,forge] —
    edit a skill in a class context, possibly moving it to a new slot."""
    (class_id, prev_slot, slot, skill_id, action, name, desc, icon, data, forge_) = (p + [None] * 10)[:10]
    sid = int(skill_id)
    if _skill_row(conn, sid) is None:
        return _err(f"Skill {sid} not found.")
    _write_skill(conn, sid, action, name, desc, icon, slot, data, forge_)
    cid, ps, ns = int(class_id), int(prev_slot or 0), int(slot or 0)
    _set_class_slot(conn, cid, ns, sid)
    if ps != ns:                              # moved slots: clear the old one
        conn.execute("DELETE FROM class_skills WHERE class_id=? AND slot=?", (cid, ps))
    conn.commit()
    return {"Cmd": "sfUpdate", "Name": name, "OldSlot": ps, "Slot": ns, "Skill": sid,
            "Data": skill_object(_skill_row(conn, sid))}


def sf_clone(conn, p):
    """sfClone[classID,skillID] — copy a skill into a new id on the class."""
    class_id, skill_id = (p + [None] * 2)[:2]
    src = _skill_row(conn, int(skill_id))
    if src is None:
        return _err(f"Skill {skill_id} not found.")
    cid = int(class_id)
    new_id = _next_skill_id(conn)
    slot = _next_free_slot(conn, cid)
    _write_skill(conn, new_id, src["action"], src["name"], src["description"],
                 src["icon"], slot, src["data"], src["forge_data"])
    _set_class_slot(conn, cid, slot, new_id)
    conn.commit()
    return {"Cmd": "sfClone", "Name": src["name"], "Slot": slot,
            "Skill": new_id, "Copy": int(skill_id)}


def sf_link(conn, p):
    """sfLink[classID,skillID] — add an existing (shared) skill to a class."""
    class_id, skill_id = (p + [None] * 2)[:2]
    sid = int(skill_id)
    src = _skill_row(conn, sid)
    if src is None:
        return _err(f"Skill {sid} not found.")
    cid = int(class_id)
    slot = _next_free_slot(conn, cid)
    _set_class_slot(conn, cid, slot, sid)
    conn.commit()
    return {"Cmd": "sfLink", "Name": src["name"], "Slot": slot, "Skill": sid}


def sf_del(conn, p):
    """sfDel[classID,slot] — remove a skill from a class slot (library keeps it)."""
    class_id, slot = (p + [None] * 2)[:2]
    cid, s = int(class_id), int(slot or 0)
    row = conn.execute("SELECT skill_id FROM class_skills WHERE class_id=? AND slot=?",
                       (cid, s)).fetchone()
    name = ""
    if row is not None:
        sk = _skill_row(conn, row["skill_id"])
        name = sk["name"] if sk else ""
    conn.execute("DELETE FROM class_skills WHERE class_id=? AND slot=?", (cid, s))
    conn.commit()
    return {"Cmd": "sfRemove", "Name": name, "Slot": s}


# command -> handler, for dispatch
MUTATIONS = {
    "sfNew": sf_new, "sfNewLib": sf_new_lib, "sfEdit": sf_edit, "sfSave": sf_save,
    "sfClone": sf_clone, "sfLink": sf_link, "sfDel": sf_del,
}


def handle_mutation(conn, cmd, params):
    fn = MUTATIONS.get(cmd)
    if fn is None:
        return None
    try:
        return fn(conn, list(params or []))
    except Exception as ex:                   # never drop the editor on a bad edit
        return _err(f"{cmd} failed: {ex}")


# --- monster skills: a monster's class graph drives its telegraphed tile attacks ----------
# "However AE does it": a monster row carries a class_id pointing at a real CharacterClass, so
# the SAME SkillForge that edits player classes edits monster classes (classes_object lists
# every class). The class's skill graph holds a tile node (HitTiles/TileWave/...); the AI loop
# emits it as a MonReq and combat applies the damage when a client reports a hit.
_TILE_NODES = {"HitTiles", "HitStream", "TileWave", "TileTrack",
               "TileCluster", "TileSafe", "TileMove"}


def monster_class_id(conn, mon_id):
    """The class_id a monster's skills come from, or None if it has none."""
    try:
        row = conn.execute("SELECT class_id FROM monsters WHERE mon_id=?",
                           (int(mon_id),)).fetchone()
    except (TypeError, ValueError):
        return None
    return row["class_id"] if row and row["class_id"] is not None else None


def _graph_nodes(data):
    """The {nodeId: props} dict from a skill `data` graph ([{header}, {nodes}])."""
    if isinstance(data, list) and len(data) > 1 and isinstance(data[1], dict):
        return data[1]
    return {}


def _named_node(nodes, name):
    return next((p for p in nodes.values()
                 if isinstance(p, dict) and p.get("Name") == name), None)


def monster_skills(conn, mon_id):
    """All telegraphed tile skills for a monster's class, in slot order — the AI rotates through
    them. Each entry is {nodes, cd_ms, multiplier, name, skill_id}, where `nodes` is the LIST of
    tile-node payloads in that skill's graph (each becomes one MonReq `Response`). A single skill
    can carry several tiles fired together — Ragnafluff's 4 HitStream firewalls are one cast. The
    Cooldown/Damage nodes in the graph set that skill's cadence-to-next and hit multiplier.
    Empty list if the monster has no class or no tile skills."""
    cid = monster_class_id(conn, mon_id)
    if cid is None:
        return []
    out = []
    for r in conn.execute(
            "SELECT s.skill_id, s.name, s.data FROM class_skills cs "
            "JOIN skills s ON s.skill_id=cs.skill_id WHERE cs.class_id=? ORDER BY cs.slot",
            (int(cid),)):
        nodes = _graph_nodes(_parse(r["data"], [{}, {}]))
        cd = _named_node(nodes, "Cooldown") or {}
        # a Summon skill (server-side spawnMob — Ragnafluff's clones) instead of a telegraphed tile
        summon = _named_node(nodes, "Summon")
        if summon:
            out.append({"summon": {"mon_id": int(summon.get("MonID") or 0),
                                   "count": int(summon.get("Count") or 1),
                                   "max_alive": int(summon.get("MaxAlive")
                                                    or summon.get("Count") or 1),
                                   "hp": int(summon.get("HP") or 0),
                                   "level": int(summon.get("Level") or 1),
                                   "x": float(summon.get("X") or 0.0),
                                   "y": float(summon.get("Y") or 0.0),
                                   # optional: boss shatters the add after SelfBreakMs and stuns its
                                   # target for StunSecs (Groglurk's Mirror). 0 => normal add.
                                   "self_break_ms": int(summon.get("SelfBreakMs") or 0),
                                   "stun_secs": float(summon.get("StunSecs") or 0.0)},
                        "cd_ms": int(cd.get("CD") or 20000),
                        "name": r["name"] or "", "skill_id": r["skill_id"]})
            continue
        tiles = [dict(p) for p in nodes.values()
                 if isinstance(p, dict) and p.get("Name") in _TILE_NODES]
        if not tiles:
            continue
        dmg = _named_node(nodes, "Damage") or {}
        out.append({"nodes": tiles,
                    "cd_ms": int(cd.get("CD") or 5000),
                    "multiplier": float(dmg.get("Multiplier") or 1.0),
                    "name": r["name"] or "", "skill_id": r["skill_id"]})
    return out


def build_init(conn):
    """The full s2c `sfInit` payload that opens the Forge."""
    pal = load_palette()
    return {
        "Cmd": "sfInit",
        "headers": pal["headers"],
        "nodes": pal["nodes"],
        "helpers": pal["helpers"],
        "conditionals": pal["conditionals"],
        "activators": pal["activators"],
        "classes": classes_object(conn),
        "skills": all_skills(conn),
    }
