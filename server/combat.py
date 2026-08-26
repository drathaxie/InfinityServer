"""
Combat — Stage 1: authoritative auto-attack so monsters are killable.

Reverse-engineered protocol (from the capture):
  c2s gar [slot, target]                         begin skill in slot on target
  c2s gai [slot, ctx, nodeName, ...args]         drive a node (e.g. Range/validate/target)
  s2c Attack {Caster, Slot, StatusCode, Nodes}   server-authoritative result; the
        Damage node = {DamageTypes, Damages:[n], Targets:[ts], TargetHPs:[remaining]}
  s2c entityDeath / mKill / addGoldXP            on kill

Stage 1 scope: slot-0 auto-attack only. The server owns monster HP, rolls its own
damage (not AE's formula), and emits a valid Attack. Skills (slots 1-5) get a benign
ack for now; real skill node-graphs come with the Skill Forge work (Stage 3).
"""
import math
import random
import time
import uuid

AUTO_CD = 0.55          # seconds between auto-attack hits (matches ~600ms client CD)
DEFAULT_HP = 1000       # fallback if we never saw the monster's spawn HP
GOLD_PER_KILL = 15
XP_PER_KILL = 20
RESPAWN_DELAY = 8.0     # seconds a monster stays dead before RespawnMon

_mon = {}               # (area, "m:ID") -> current HP
_maxhp = {}             # (area, "m:ID") -> max HP (for respawn)
_moninfo = {}           # (area, "m:ID") -> {"mon_id": catalogID, "frame": cell}
_last = {}              # caster uid -> last auto-attack time
_aggro = {}             # (area, "m:ID") -> {"uid": target uid, "last": ts} (autonomous AI)


_area_moncat = {}           # area -> {MonMapID: (catalog MonID, monster name)}


def register_area_monsters(area, monbranch):
    """From the AreaJoin monBranch, map each MonMapID -> (catalog MonID, name). The captured
    cell entities carry only the instance id (m:<MonMapID>), so this is how a kill resolves to
    its real catalog monster + name for killcount quest credit."""
    if not isinstance(monbranch, list):
        return
    mp = {}
    for e in monbranch:
        try:
            mmid = int(e.get("MonMapID"))
        except (TypeError, ValueError):
            continue
        try:
            mid = int(e.get("MonID"))
        except (TypeError, ValueError):
            mid = None
        mp[mmid] = (mid, e.get("strMonName") or e.get("Name") or "")
    if mp:
        _area_moncat[area] = mp


def monster_identity(area, target_string):
    """(catalog MonID, name) for a live target m:<MonMapID>. register_monster (the moveToCell
    path) stores the mon_id but NOT the name, so fall back to the area monBranch mapping
    (register_area_monsters), which carries strMonName, for the name. Killcount quest credit
    relies on this name, so an empty name here is what made any kill credit any quest.
    (None, '') if unknown."""
    branch = (None, "")
    if target_string and target_string.startswith("m:"):
        try:
            mmid = int(target_string[2:])
            branch = (_area_moncat.get(area) or {}).get(mmid, (None, ""))
        except ValueError:
            return None, ""
    info = _moninfo.get((area, target_string))
    if info and info.get("mon_id"):
        return info.get("mon_id"), info.get("name") or branch[1] or ""
    return branch


def monster_catalog_id(area, target_string):
    """The catalog MonID for a live target (m:<mapID>) — used to credit killcount quests."""
    return monster_identity(area, target_string)[0]


def register_monster(area, target_string, hp, mon_id=None, frame=None, level=None,
                     race=None, element=None, x=None, y=None):
    """Learn a monster's HP/identity from the CellJoin we serve, so the HP bar stays sane,
    we can re-spawn it (RespawnMon needs the catalog id + frame), and its swing scales with
    its level (P1-3). Position is retained for targetless mobile casts; race/element are
    stored for later (Dragon's Bane vs dragons = P2-3)."""
    if not target_string or not target_string.startswith("m:"):
        return
    key = (area, target_string)
    if key not in _mon:
        _mon[key] = int(hp or DEFAULT_HP)
        _maxhp[key] = int(hp or DEFAULT_HP)
    info = _moninfo.setdefault(key, {})
    if mon_id is not None:
        info["mon_id"] = mon_id
    if frame is not None:
        info["frame"] = frame
    if level is not None:
        try:
            info["level"] = int(level)
        except (TypeError, ValueError):
            pass
    if race is not None:
        info["race"] = race
    if element is not None:
        info["element"] = element
    try:
        if x is not None and y is not None:
            info["x"] = float(x)
            info["y"] = float(y)
    except (TypeError, ValueError):
        pass


def target_from_params(params):
    for x in reversed(params or []):
        if isinstance(x, str) and x.startswith("m:"):
            return x
    return None


def auto_attack(area, target, uid):
    """Return (attack_packet, killed_bool, damage). Honors the auto-attack cooldown:
    off-cooldown ticks just re-assert Range (no damage) so the client stays fed."""
    caster = f"p:{uid}"
    nodes = [{"Name": "Range", "HRange": 31.0, "VRange": 31.0, "Target": target, "Charge": False}]
    now = time.time()
    killed = False
    dmg = 0
    if target and now - _last.get(uid, 0.0) >= AUTO_CD:
        _last[uid] = now
        key = (area, target)
        # stat-based hit (ap weapon roll + crit/miss), unified with the graph path (P3-4);
        # falls back to the flat roll only for an unregistered caster (inside _hit).
        dmg, dtype = _hit(caster, _dragon_bonus(caster, area, target), False)
        prev = _mon.get(key, DEFAULT_HP)
        hp = max(0, prev - dmg)
        _mon[key] = hp
        nodes.append({"Name": "Damage", "DamageTypes": [dtype], "Damages": [dmg],
                      "Targets": [target], "TargetHPs": [hp]})
        nodes.append({"Name": "PlayerAnimation", "Animation": "Attack1",
                      "Priority": "Normal", "Targets": 1})
        nodes.append({"Name": "Cooldown", "Animation": "", "Slot": 0, "CD": 600})
        if prev > 0 and hp <= 0:        # only the blow that downs a LIVE monster is the kill —
            killed = True               # attacking an already-dead one must not re-credit it
    attack = {"Cmd": "Attack", "Caster": caster, "Slot": 0,
              "StatusCode": 1, "Error": "", "Nodes": nodes}
    return attack, killed, dmg


# --- Stage 3: authored skill graphs (slots 1-5) drive combat ----------------
# The Forge stores each skill as Data=[headers,nodes] + ForgeData=[positions,tree].
# The tree (ForgeData[1]) is the execution order: header -> Next -> node -> Next...
# We walk it, turn each AUTHORED node into the RESOLVED node the client's
# Node.Execute renders (Damage gets computed Damages/TargetHPs), and emit a single
# Attack(Success) — the same single-shot shape the proven auto-attack uses, so no
# igai/gai round-trip is needed for a basic cast.
# (Removed the dead `DTYPE` map (P3-3): it conflated element with the popup enum — element
# is read directly via DamageType=="Magical", and the resolved Damage DamageTypes is the
# popup kind 0/1/2/3/5, not 0/1/2 element. It was never referenced.)


def _walk_graph(data, forge):
    """Ordered [(nodeId, props), ...] by following the header's Next chain in the
    ForgeData tree, reading each node's authored props from Data[1]. Tolerant of
    missing/short graphs (returns [])."""
    nodes = data[1] if isinstance(data, list) and len(data) > 1 and isinstance(data[1], dict) else {}
    tree = forge[1] if isinstance(forge, list) and len(forge) > 1 and isinstance(forge[1], dict) else {}
    order = []
    seen = set()
    for _hid, hnode in tree.items():            # first (only) header chain for v1
        nxt = hnode.get("Next") if isinstance(hnode, dict) else None
        while isinstance(nxt, dict) and nxt.get("id") and nxt["id"] not in seen:
            nid = nxt["id"]
            seen.add(nid)
            order.append((nid, nodes.get(nid, {}) or {}))
            nxt = nxt.get("Next")
        break
    return order, nodes


def alive_monsters(area):
    """Every monster currently alive in an area (from the HP we track). Used by the
    AllEnemies helper for server-side AoE — no client hitbox round-trip needed."""
    return [ts for (a, ts), hp in _mon.items() if a == area and hp > 0]


HEAL_MAX_TARGETS = 4        # Healing Word: caster + up to 3 nearby allies (tooltip)
PALADIN_MAX_TARGETS = 6     # Protection/Guard/lifelink: caster + up to 5 allies (tooltip)
METEOR_MAX_TARGETS = 4      # InfinityHero Meteor tooltip: up to 4 targets


def _ally_targets(caster, allies):
    """Caster first, then the other players in the area (deduped). Heals/ally-buffs land
    on this set; in single-player it collapses to just the caster (which is the common
    captured case: a lone `[-345] -> [p:self]` heal)."""
    out = [caster]
    for a in (allies or []):
        if a and a != caster and a not in out:
            out.append(a)
    return out


def _resolve_targets(props, nodes, caster, default_targets, area, allies=None, heal=False):
    """A Damage/Aura/Restrict/MoveTargets Targets ref is {id: helperNodeId}. The helper
    decides who: Self = caster, Allies = caster + area allies (party heal/buff),
    AllEnemies = every monster in the area (server-side AoE), Target/none = `default_targets`
    — the entities the preceding input node resolved (streaming handshake) or [cast target]
    (single-shot). A heal NEVER lands on the clicked monster: with no ally helper it still
    resolves to caster + allies."""
    dts = [t for t in (default_targets or []) if t]
    name = ""
    t = props.get("Targets")
    if isinstance(t, dict) and t.get("id") is not None:
        name = (nodes.get(str(t["id"]), {}) or {}).get("Name", "").lower()
    if name == "self":
        return [caster]
    if name in ("allies", "allallies", "party", "allinrangeallies"):
        return _ally_targets(caster, allies)
    if name in ("allenemies", "allinrange", "area"):
        return alive_monsters(area) or dts
    if heal:                                    # a heal targets allies/self, never the monster
        return _ally_targets(caster, allies)
    return dts or [caster]


def _roll(mult):
    dmg = int(round(random.randint(18, 55) * mult))
    if random.random() < 0.12:
        dmg *= 2
    return max(1, dmg)


def _render_node(area, slot, caster, default_targets, nodes, props,
                 det_total=None, empower_mult=1.0, allies=None):
    """Render ONE authored node into the resolved Attack node the client executes (or
    None to skip). Returns (node|None, dmg, killed[list of m: that died this node]).
    det_total/empower_mult carry this cast's Determination outcome (computed once per cast);
    allies = the area's other players (for heal/ally-buff targeting)."""
    name = props.get("Name")
    killed = []
    if name == "Damage":
        mult = float(props.get("Multiplier") or 0) or 1.0       # unset -> normal (1x)
        mult *= empower_mult                # Determined empower / Conviction stacks (1.0 if not)
        if _is_heal(props):
            # A HEAL is a Damage node with NEGATIVE Damages on ally/self p: targets; the
            # client renders HP<0 as a green popup and the raised TargetHP applies the heal
            # (BattleTextBouncer.CreateDamage: HP<0 -> popupHeal/popupHealCrit). Heals land on
            # players ONLY — the inverse of the offensive gate below.
            cap = int(props.get("MaxTargets") or HEAL_MAX_TARGETS)
            tgts = _resolve_targets(props, nodes, caster, default_targets, area, allies, heal=True)
            ptgts = [t for t in tgts if isinstance(t, str) and t.startswith("p:")][:cap]
            if not ptgts:
                return (None, 0, killed)
            damages, hps, dtypes = [], [], []
            for ts in ptgts:
                amt, crit = _heal_amount(caster, mult)
                new = _raise_hp(_uid_of(ts), amt)
                damages.append(-amt); hps.append(new); dtypes.append(1 if crit else 0)
            return ({"Name": "Damage", "DamageTypes": dtypes, "Damages": damages,
                     "Targets": ptgts, "TargetHPs": hps}, 0, killed)
        magical = props.get("DamageType") == "Magical"          # element -> which power (ap/sp)
        guaranteed = bool(props.get("Guaranteed"))               # skip the to-hit/dodge roll
        tgts = _resolve_targets(props, nodes, caster, default_targets, area, allies)
        # Offensive Damage only ever hits MONSTERS — never a player. (A Damage node that
        # resolves to the caster/an ally — e.g. an offensive skill cast with no monster
        # target — must NOT send TargetHPs:[0] for a player, which the client reads as
        # "set HP to 0" and kills them. Heals are the explicit Heal branch above.)
        mtgts = [t for t in tgts if isinstance(t, str) and t.startswith("m:")]
        # Authored AoE skills can cap their server-resolved target set. Without this, an
        # AllEnemies helper hits the entire room even when the tooltip promises a smaller cap
        # (InfinityHero's Meteor is explicitly limited to four targets).
        try:
            cap = int(props.get("MaxTargets")) if props.get("MaxTargets") is not None else None
        except (TypeError, ValueError):
            cap = None
        if cap is not None:
            mtgts = mtgts[:max(0, cap)]
        if not mtgts:
            return (None, 0, killed)                             # nothing to damage -> skip node
        damages, hps, dtypes, dmg = [], [], [], 0
        for ts in mtgts:
            # Dragon's Bane: +20%/+50% vs Dragonkin for a Dragonslayer (per-target, P2-3)
            d, dtype = _hit(caster, mult * _dragon_bonus(caster, area, ts), magical, guaranteed)
            key = (area, ts)
            prev = _mon.get(key, DEFAULT_HP)
            hp = max(0, prev - d)
            _mon[key] = hp
            if prev > 0 and hp <= 0:        # don't re-credit an already-dead target
                killed.append(ts)
            damages.append(d); hps.append(hp); dtypes.append(dtype); dmg += d
        return ({"Name": "Damage", "DamageTypes": dtypes, "Damages": damages,
                 "Targets": mtgts, "TargetHPs": hps}, dmg, killed)
    if name == "Cooldown":
        return ({"Name": "Cooldown", "Animation": props.get("Animation") or "",
                 "Slot": slot, "CD": int(props.get("CD") or 0) or 1000}, 0, killed)
    if name == "Resource":
        # Determination is owned by the server (built/spent per cast in _apply_determination);
        # the Resource node just reports the post-cast total to the client (NodeResource sets
        # RP to this). Falls back to the live total if a cast didn't compute one.
        uid = int(caster.split(":")[1]) if ":" in caster else None
        total = det_total if det_total is not None else (_rp.get(uid, 0) if uid is not None else 0)
        return ({"Name": "Resource", "Amount": total}, 0, killed)
    if name == "Range":
        return ({"Name": "Range", "HRange": float(props.get("HRange") or 5),
                 "VRange": float(props.get("VRange") or 1),
                 "Target": (default_targets[0] if default_targets else ""),
                 "Charge": False, "StayAtMaxRange": False}, 0, killed)
    if name in ("AnimationHitbox", "Hitbox"):
        return ({"Name": name, "X": float(props.get("X") or 0), "Y": float(props.get("Y") or 0),
                 "Width": int(props.get("Width") or 6), "Height": int(props.get("Height") or 2),
                 "Animation": props.get("Animation") or "", "Speed": float(props.get("Speed") or 1),
                 "Time": float(props.get("Time") or 0), "Targets": list(default_targets)}, 0, killed)
    if name == "PlayerAnimation":
        return ({"Name": "PlayerAnimation", "Animation": props.get("Animation") or "Attack1",
                 "Priority": "Attack", "Speed": 1.0, "Targets": 1}, 0, killed)
    if name == "SoundFX":
        return ({"Name": "SoundFX", "Animation": props.get("Animation") or "",
                 "Sound": props.get("Sound") or "", "Time": float(props.get("Time") or 0),
                 "MinPitch": 0.0, "MaxPitch": 0.0}, 0, killed)
    if name == "Particle":
        # Most legacy graphs author caster-attached particles without a Targets helper.
        # Newer effects can wire Target/AllEnemies explicitly (the InfinityHero sword
        # composite must be instantiated over the victim, not over the caster).
        tgts = ([caster] if not props.get("Targets") else
                _resolve_targets(props, nodes, caster, default_targets, area, allies))
        node = {"Name": "Particle", "Follow": props.get("Follow") or "No Follow",
                "X": float(props.get("X") or 0), "Y": float(props.get("Y") or 0),
                "Particle": props.get("Particle") or "", "Animation": props.get("Animation") or "",
                "Time": str(props.get("Time") or "0"), "Targets": tgts}
        if props.get("AnimSpeed") is not None:
            node["AnimSpeed"] = float(props["AnimSpeed"])
        if props.get("Lifetime") is not None:
            node["Lifetime"] = float(props["Lifetime"])
        return (node, 0, killed)
    if name == "SpellAnimation":
        target = default_targets[0] if default_targets else caster
        return ({"Name": "SpellAnimation", "FX": props.get("FX") or "ORIGIN",
                 "Animation": props.get("Animation") or "",
                 "SpellGraphic": props.get("SpellGraphic") or "",
                 "SpellImpact": props.get("SpellImpact") or "",
                 "AttachInit": props.get("AttachInit") or "CastAttach",
                 "Attach": props.get("Attach") or "Cast",
                 "AttachImpact": props.get("AttachImpact") or "Origin",
                 "Follow": bool(props.get("Follow")),
                 "X": float(props.get("X") or 0), "Y": float(props.get("Y") or 0),
                 "Ease": props.get("Ease"), "ProjSpeed": float(props.get("ProjSpeed") or 0),
                 "target": target}, 0, killed)
    if name == "AnimationCancel":
        return ({"Name": "AnimationCancel"}, 0, killed)
    if name == "Aura":
        aura_fx = AURA_FX.get(props.get("AuraName")) or {}
        if aura_fx.get("self_only"):
            tgts = [caster]
        elif aura_fx.get("kind") == "guard":
            # a friendly party buff lands on the caster + allies, never the cast target
            cap = int(props.get("MaxTargets") or PALADIN_MAX_TARGETS)
            tgts = _ally_targets(caster, allies)[:cap]
        else:
            tgts = _resolve_targets(props, nodes, caster, default_targets, area, allies)
            if (props.get("Targets") or {}).get("id") is None and not default_targets:
                tgts = [caster]                 # self-buff auras default to the caster
        apply_aura(area, props.get("AuraName"), tgts, caster)   # DoT/HoT/debuff effect (P2-4)
        return ({"Name": "Aura", "Hide": False, "Animation": props.get("Animation") or "",
                 "AuraName": props.get("AuraName") or "", "Targets": tgts,
                 "casterTS": caster, "uniquenessType": 1}, 0, killed)
    if name == "AuraVFX":
        return ({"Name": "AuraVFX", "AuraName": props.get("AuraName") or "",
                 "VFX": props.get("VFX") or ""}, 0, killed)
    if name == "Restrict":
        tgts = _resolve_targets(props, nodes, caster, default_targets, area, allies)
        return ({"Name": "Restrict", "Direction": True, "Movement": bool(props.get("Movement")),
                 "Skills": bool(props.get("Skills")), "Slot": props.get("Slot") or "",
                 "Animation": props.get("Animation") or "",
                 "Time": float(props.get("Duration") or props.get("Time") or 0) or 0.3,
                 "Targets": tgts}, 0, killed)
    if name == "Interruptable":
        return ({"Name": "Interruptable", "Animation": props.get("Animation") or "",
                 "Time": float(props.get("Time") or 0)}, 0, killed)
    if name == "MoveTargets":
        tgts = _resolve_targets(props, nodes, caster, default_targets, area, allies)
        return ({"Name": "MoveTargets", "Mode": props.get("Mode") or "Pull",
                 "Distance": float(props.get("Distance") or 0) or 2.0, "Targets": tgts}, 0, killed)
    if name == "DashToTarget":
        return ({"Name": "DashToTarget", "Animation": props.get("Animation") or "",
                 "Targets": list(default_targets)}, 0, killed)
    if name == "DispenseDamage":
        return ({"Name": "DispenseDamage"}, 0, killed)
    if name == "MonTransform":
        # Morph the caster into a monster prefab (NodeMonTransform: Bundle+Linkage+Scale, or
        # detransform:true to revert). Broadcasts with the Attack, so the whole area sees the
        # form. The revert is driven by a transform-flagged guard aura's expiry (aura_ticks).
        if props.get("detransform"):
            return ({"Name": "MonTransform", "detransform": True}, 0, killed)
        return ({"Name": "MonTransform", "Bundle": props.get("Bundle"),
                 "Linkage": props.get("Linkage") or "",
                 "Scale": float(props.get("Scale") or 1)}, 0, killed)
    if name == "UpdateAnimation":
        return ({"Name": "UpdateAnimation", "Tag": "combatIdle",
                 "Value": props.get("Value") or "2H_Fight"}, 0, killed)
    return None, 0, killed                      # unknown -> skip


INFINITY_METEOR_SKILL_ID = 90370
INFINITY_ASPECT_SKILLS = {90371: "healer", 90372: "warrior"}
_active_aspect = {}            # uid -> "warrior" | "healer"; Warrior is the default


def active_aspect(uid):
    """The InfinityHero aspect used by the caster's next aspect-sensitive skill."""
    return _active_aspect.get(uid, "warrior")


def set_active_aspect(uid, aspect):
    """Select an InfinityHero aspect. Exposed for class-switch/setup code and focused tests."""
    if aspect not in ("warrior", "healer"):
        raise ValueError("aspect must be 'warrior' or 'healer'")
    _active_aspect[uid] = aspect


def _meteor_damage_props(uid, props):
    """Apply Meteor's active-Aspect branch to one authored Damage node."""
    out = dict(props)
    if active_aspect(uid) == "warrior":
        # Warrior: clicked target only, with the tooltip's 50% increased damage.
        out.pop("Targets", None)
        out["MaxTargets"] = 1
        out["Multiplier"] = float(out.get("Multiplier") or 1.0) * 1.5
    else:
        # Healer: retain the graph's AllEnemies helper, but never exceed four targets.
        out["MaxTargets"] = min(int(out.get("MaxTargets") or METEOR_MAX_TARGETS),
                                METEOR_MAX_TARGETS)
    return out


def _meteor_aspect_node(area, uid, caster, resolved_nodes):
    """Apply and render Meteor's post-hit Aspect effect for the monsters it actually hit."""
    targets = []
    for node in resolved_nodes:
        if node.get("Name") != "Damage":
            continue
        for ts, dmg in zip(node.get("Targets") or [], node.get("Damages") or []):
            if dmg >= 0 and ts.startswith("m:") and ts not in targets and monster_alive(area, ts):
                targets.append(ts)
    aspect = active_aspect(uid)
    targets = targets[:1] if aspect == "warrior" else targets[:METEOR_MAX_TARGETS]
    if not targets:
        return None
    aura_name = "Burning Field" if aspect == "warrior" else "Suppression"
    apply_aura(area, aura_name, targets, caster)
    return {"Name": "Aura", "Hide": False, "Animation": "", "AuraName": aura_name,
            "Targets": targets, "casterTS": caster, "uniquenessType": 1}


def cast_skill(area, uid, slot, target, data, forge, skill_id=None, allies=None):
    """Single-shot walk of a graph with NO input nodes -> (attack, killed_list, dmg).
    Unauthored/empty graphs fall back to a default hit so the slot still does something.
    Graphs WITH input nodes go through the streaming engine (begin_cast) instead."""
    caster = f"p:{uid}"
    order, nodes = _walk_graph(data or [], forge or [])
    selected_aspect = INFINITY_ASPECT_SKILLS.get(skill_id)
    if selected_aspect:
        set_active_aspect(uid, selected_aspect)
    det_total, empowered = _apply_determination(uid, slot, skill_id)
    empower_mult, post, hits = _empower(skill_id, empowered)
    empower_mult *= _conviction_mult(uid, skill_id)    # Paladin per-stack scaling (1.0 else)
    out, killed, total = [], [], 0
    for _nid, props in order:
        if skill_id == INFINITY_METEOR_SKILL_ID and props.get("Name") == "Damage":
            props = _meteor_damage_props(uid, props)
        rendered, dmg, k = _render_nodes(area, slot, caster, [target] if target else [],
                                         nodes, props, det_total, empower_mult, hits, allies,
                                         skill_id)
        out.extend(rendered)
        total += dmg
        killed += k
    if skill_id == INFINITY_METEOR_SKILL_ID:
        aspect_node = _meteor_aspect_node(area, uid, caster, out)
        if aspect_node is not None:
            out.append(aspect_node)
    if post:                                        # empowered self-heal / target stun
        fx_node = _empower_node(skill_id, uid, caster, area, [target] if target else [])
        if fx_node is not None:
            out.append(fx_node)
    ll = _lifelink_node(skill_id, caster, total, allies)   # Smite: heal party from damage dealt
    if ll is not None:
        out.append(ll)
    if det_total is not None:
        # Report the post-cast resource as the LAST node so it's the final bar-set instruction —
        # a Resource sitting mid-list (before the lifelink heal / UpdateAnimation) left the client
        # showing the pre-cast total until the next cast (the Smite "stacks didn't drop" report).
        out = [n for n in out if n.get("Name") != "Resource"]
        out.append({"Name": "Resource", "Amount": det_total})
    if not order and target:
        # ONLY an unauthored/empty graph deals a default hit — authored buff skills with
        # no Damage node (e.g. Dragon's Bane) must not inject a phantom hit.
        d, dtype = _hit(caster, 1.0, False)
        key = (area, target)
        prev = _mon.get(key, DEFAULT_HP)
        hp = max(0, prev - d)
        _mon[key] = hp
        if prev > 0 and hp <= 0:            # don't re-credit an already-dead target
            killed.append(target)
        total += d
        out.insert(0, {"Name": "Damage", "DamageTypes": [dtype], "Damages": [d],
                       "Targets": [target], "TargetHPs": [hp]})
    if not any(n["Name"] == "Cooldown" for n in out):
        out.append({"Name": "Cooldown", "Animation": "", "Slot": slot, "CD": 1500})
    attack = {"Cmd": "Attack", "Caster": caster, "Slot": slot, "StatusCode": 1,
              "Wait": True, "Error": "", "Nodes": out}
    return attack, killed, total


# --- streaming skill engine (igai/gai handshake) -----------------------------
# The server owns the graph + damage math; the client resolves spatial INPUT nodes
# (which entities are actually in the Range/Hitbox) and animation timing. Flow:
#   c2s gar[slot,target] -> server walks; at each input node sends s2c igai{Response,
#   ContextId} and PAUSES -> c2s gai[slot,ctx,Name,...targets] -> server renders that
#   node + following non-input nodes as an Attack batch (Pending, or Success at the end)
#   and sends the next igai. Damage in a batch hits the targets the client just returned.

# Nodes the client resolves spatially via igai/gai (the 4 confirmed in capture). Dash/
# DashToTarget render inline (the capture shows them in the Attack batch, no igai).
#   Range/RangeMulti  -> validate the cast target (ReturnType absent ~ 0).
#   AnimationHitbox   -> the swing's spatial box; ReturnType 1; the client plays the
#                        animation, then at the box frame answers the gai with the entities
#                        actually hit (1-3 monsters in capture = real cleave).
#   Hitbox            -> an immediate BoxCastAll; ReturnType 2; same gai-answer handshake.
# The client DOES answer the AnimationHitbox/Hitbox gai (ContextId round-trips in capture) —
# the earlier "the client never sends it" was wrong; the handshake is async (after the box
# frame), and resume_cast parses the returned target SET into the Damage node (P1-1).
INPUT_NODES = {"Range", "RangeMulti", "AnimationHitbox", "Hitbox"}
NS_SUCCESS, NS_PENDING = 1, 2

_casts = {}                 # ContextId -> _Cast (a paused walk awaiting gai)


class _Cast:
    def __init__(self, area, uid, slot, target, order, nodes, skill_id=None, allies=None):
        self.area, self.uid, self.slot, self.target = area, uid, slot, target
        self.skill_id = skill_id
        self.caster = f"p:{uid}"
        self.order, self.nodes = order, nodes
        self.allies = allies
        self.i = 0
        self.targets = [target] if target else []
        self.total = 0
        # Standalone AuraChange removals caused by Mage combo consumption are
        # emitted immediately after the Attack batch that consumed the aura.
        self.side_packets = []
        # Resource mutation is deferred until the client validates the first
        # input node. Disconnects/cancelled Range checks therefore spend nothing.
        self.resource_applied = False
        self.det_total = None
        self.empower_mult, self.post, self.hits = 1.0, None, 1


def _apply_cast_resource(cast):
    if cast.resource_applied:
        return
    cast.det_total, empowered = _apply_determination(
        cast.uid, cast.slot, cast.skill_id)
    cast.empower_mult, cast.post, cast.hits = _empower(cast.skill_id, empowered)
    cast.empower_mult *= _conviction_mult(cast.uid, cast.skill_id)
    cast.resource_applied = True


def _new_ctx(cast):
    ctx = uuid.uuid4().hex[:8]
    _casts[ctx] = cast
    return ctx


def _attack(cast, nodes, status):
    return {"Cmd": "Attack", "Caster": cast.caster, "Slot": cast.slot,
            "StatusCode": status, "Wait": True, "Error": "", "Nodes": nodes}


def _igai(cast, props, ctx):
    """The input-resolution request for one node, mirroring the captured igai shapes exactly:
      Range          ReturnType 0  {hrange,vrange,mode:validate,target,charge,stayAtMaxRange,type}
      RangeMulti     ReturnType 0  {hrange,vrange,target,max}
      AnimationHitbox ReturnType 1 {X,Y,Width,Height,Animation,Speed,Time,inputReturn:1}
      Hitbox         ReturnType 2  {X,Y,Width,Height,inputReturn:2}   (no Animation/Speed/Time)
    The client answers c2s gai[slot,ctx,Name,...targets]; resume_cast resolves the targets."""
    name = props.get("Name")
    resp = {"Name": name, "slot": cast.slot}
    rt = 0
    box = {"X": float(props.get("X") or 0), "Y": float(props.get("Y") or 0),
           "Width": int(props.get("Width") or 6), "Height": int(props.get("Height") or 2)}
    if name in ("Range", "ConditionalRange"):
        resp.update({"hrange": float(props.get("HRange") or 5), "vrange": float(props.get("VRange") or 1),
                     "mode": "validate", "target": cast.target, "charge": False,
                     "stayAtMaxRange": False, "type": "Hostile"})
    elif name == "RangeMulti":
        resp.update({"hrange": float(props.get("HRange") or 5), "vrange": float(props.get("VRange") or 1),
                     "target": cast.target, "max": int(props.get("Max") or HEAL_MAX_TARGETS)})
    elif name == "AnimationHitbox":
        rt = 1
        resp.update(box)
        resp.update({"Animation": props.get("Animation") or "", "Speed": float(props.get("Speed") or 1),
                     "Time": float(props.get("Time") or 0), "inputReturn": 1})
    elif name == "Hitbox":
        rt = 2
        resp.update(box)
        resp["inputReturn"] = 2
    else:
        resp.update({"target": cast.target})
    return {"Cmd": "igai", "Caster": cast.caster, "Response": resp,
            "ReturnType": rt, "ContextId": ctx}


def _render_batch(cast, include_current):
    """Render nodes from cast.i: optionally the current (just-resolved input) node, then
    every following non-input node, stopping at the next input node. Advances cast.i.
    Returns (batch, killed)."""
    if include_current:
        _apply_cast_resource(cast)
    batch, killed = [], []
    first = True
    while cast.i < len(cast.order):
        nm = cast.order[cast.i][1].get("Name")
        if nm in INPUT_NODES and not (first and include_current):
            break
        rendered, dmg, k = _render_nodes(cast.area, cast.slot, cast.caster,
                                         cast.targets, cast.nodes, cast.order[cast.i][1],
                                         cast.det_total, cast.empower_mult, cast.hits,
                                         cast.allies, cast.skill_id, cast.side_packets)
        batch.extend(rendered)
        cast.total += dmg
        killed += k
        cast.i += 1
        first = False
    return batch, killed


_cast_last = {}             # (uid, slot) -> last cast time (server-side cooldown gate)


def skill_cooldown_ms(data, forge):
    """The Cooldown node's CD (ms) in a skill graph, or 0 if none."""
    order, _ = _walk_graph(data or [], forge or [])
    for _id, p in order:
        if p.get("Name") == "Cooldown":
            return int(p.get("CD") or 0)
    return 0


def off_cooldown(uid, slot, cd_ms):
    """True (and arms the timer) if the slot is ready; False if still cooling down.
    Server-authoritative gate so casts can't be spammed faster than their cooldown."""
    if cd_ms <= 0:
        return True
    now = time.time()
    if now - _cast_last.get((uid, slot), 0.0) < cd_ms / 1000.0:
        return False
    _cast_last[(uid, slot)] = now
    return True


def release_cooldown(uid, slot):
    """Undo admission for a cast the client explicitly rejected/cancelled."""
    _cast_last.pop((uid, slot), None)


def has_graph(data, forge):
    """Whether a skill actually has authored nodes (vs an empty [{},{}] graph)."""
    return bool(_walk_graph(data or [], forge or [])[0])


def begin_cast(area, uid, slot, target, data, forge, skill_id=None, allies=None):
    """Handle c2s gar. Returns (packets, killed_list, total_dmg). A class carrying a rule
    config resolves through the DATA path; otherwise graphs with no input nodes resolve in
    one Attack (cast_skill) and graphs with input nodes start the handshake."""
    cfg = class_rules(uid, skill_id)
    if cfg is not None:
        return cast_skill_rules(area, uid, slot, target, data, forge, skill_id, cfg, allies)
    order, nodes = _walk_graph(data or [], forge or [])
    if not any(p.get("Name") in INPUT_NODES for _id, p in order):
        attack, killed, dmg = cast_skill(area, uid, slot, target, data, forge, skill_id, allies)
        return [attack], killed, dmg
    cast = _Cast(area, uid, slot, target, order, nodes, skill_id, allies)
    packets = []
    batch, killed = _render_batch(cast, include_current=False)   # leading non-input nodes
    if batch:
        packets.append(_attack(cast, batch, NS_PENDING))
    packets.append(_igai(cast, cast.order[cast.i][1], _new_ctx(cast)))
    return packets, killed, cast.total


def cast_skill_rules(area, uid, slot, target, data, forge, skill_id, cfg, allies=None):
    """DATA path: resolve one cast from the class's rule config -> (packets, killed, dmg).
    Imported lazily so combat.py keeps working if combat_engine is ever absent, and so a
    failure here falls back to the Python path rather than eating the player's cast."""
    try:
        from combat_engine import live
    except Exception as exc:                                    # pragma: no cover
        print(f"  [rules] engine unavailable ({exc})")
        if resource_model(uid) in ("heroic", "stacking"):
            return [cast_failure(uid, slot, "Combat engine unavailable; please reconnect.")], [], 0
        print("  [rules] using parity-tested Python fallback")
        set_class_rules(uid, None)
        return begin_cast(area, uid, slot, target, data, forge, skill_id, allies)
    try:
        return live.cast_skill_data(area, uid, slot, target, data, forge, skill_id, cfg,
                                    allies=allies, stats=_power.get(uid))
    except Exception as exc:
        # Conviction classes retain a parity-tested Python implementation. Pure-data
        # Heroic/stacking classes do not: falling through would silently treat their
        # pools as Determination and corrupt the fight, so those fail closed instead.
        import traceback
        print(f"  [rules] cast failed for uid={uid} skill={skill_id}: {exc!r}")
        traceback.print_exc()
        if resource_model(uid) in ("heroic", "stacking"):
            return [cast_failure(
                uid, slot, "Combat skill configuration failed; please re-equip your class.")], [], 0
        print("  [rules] using parity-tested Python fallback")
        set_class_rules(uid, None)
        return begin_cast(area, uid, slot, target, data, forge, skill_id, allies)


def resume_cast(ctx, gai_params):
    """Handle c2s gai. Returns (packets, killed_list, total_dmg). Empty if no paused cast
    matches (e.g. auto-attack's gai, which we resolve single-shot)."""
    cast = _casts.pop(ctx, None)
    if cast is None:
        return [], [], 0
    current_name = cast.order[cast.i][1].get("Name") if cast.i < len(cast.order) else ""
    response_tokens = {str(x).strip().lower() for x in gai_params[3:]}
    if current_name in ("Range", "ConditionalRange", "RangeMulti") and \
            response_tokens.intersection({"false", "cancel", "cancelled", "canceled"}):
        release_cooldown(cast.uid, cast.slot)
        return [cast_failure(cast.uid, cast.slot)], [], 0
    ents = [x for x in gai_params[3:]
            if isinstance(x, str) and (x.startswith("m:") or x.startswith("p:"))]
    if current_name == "RangeMulti" and cast.skill_id == MAGE_EXPLOSION:
        # NodeRangeMulti can report the caster when a targetless mobile cast
        # starts before the client's selected-target field catches up. Explosion
        # is hostile-only: retain the server-validated primary target and accept
        # only registered living monsters from the returned set, capped at four.
        hostile = []
        for target in [cast.target] + ents:
            if valid_combat_target(cast.area, target) and target not in hostile:
                hostile.append(target)
        cast.targets = hostile[:4]
    elif ents:
        cast.targets = ents                     # the entities the client says were hit
    before = cast.total
    batch, killed = _render_batch(cast, include_current=True)
    pending = cast.i < len(cast.order)
    if not pending and cast.post:               # empowered self-heal / target stun on finish
        fx_node = _empower_node(cast.skill_id, cast.uid, cast.caster, cast.area, cast.targets)
        if fx_node is not None:
            batch.append(fx_node)
    if not pending:                             # Smite: heal party from the cast's total damage
        ll = _lifelink_node(cast.skill_id, cast.caster, cast.total, cast.allies)
        if ll is not None:
            batch.append(ll)
    packets = [_attack(cast, batch, NS_PENDING if pending else NS_SUCCESS)]
    if cast.side_packets:
        packets.extend(cast.side_packets)
        cast.side_packets = []
    if pending:
        packets.append(_igai(cast, cast.order[cast.i][1], _new_ctx(cast)))
    return packets, killed, cast.total - before


# --- Item 4: autonomous monster AI -------------------------------------------
# Monster attacks are server-driven Attack packets (Caster="m:ID", Slot=-1) — no
# client request, and no in-client monster editor exists (the Forge is class-only),
# so monster behaviour lives here. A monster aggros the player who attacks it and
# then KEEPS attacking on its own timer (driven by the server's AI loop, not by the
# player's input), until it or the player dies or the player leaves. HP is server-
# authoritative (Damage TargetHPs); a hit that drops the player to 0 is lethal and
# the caller revives them (playerRes).

PLAYER_MAXHP = 1337         # from the statUpdate sample; per-char HP comes later
# Every one of the 102 captured AE monster autos advertises a 2250 ms cooldown.
# Matching that cadence also prevents the server from piling another hit onto a
# monster whose previous attack animation is still playing.
MON_ATTACK_CD = 2.25        # seconds between a monster's swings (capture: 2250 ms)
MON_DMG = (12, 34)          # fallback hit range when a monster's level is unknown
# Monster swing scales with LEVEL (P1-3). Capture: avg monster damage ~= 7 * level across 107
# monsters (m:2027 lvl2 ~12, m:968 lvl8 ~56, m:1979 lvl12 ~71); per-hit range ~[5*lvl, 9*lvl].
MON_DMG_MIN_PER_LVL, MON_DMG_MAX_PER_LVL = 5, 9
MON_MISS_CHANCE = 0.07      # a monster's swing missing the player. Monster to-hit isn't in
                            # the capture; this lands the overall Miss popups near the captured
                            # ~4% (player ~1% from tha + the more-numerous monster swings).


def _monster_dmg(area, mon_ts):
    """A monster's hit damage, scaled by its level (P1-3) when known, else the flat range."""
    lvl = _moninfo.get((area, mon_ts), {}).get("level")
    if lvl and lvl > 0:
        return random.randint(round(lvl * MON_DMG_MIN_PER_LVL), round(lvl * MON_DMG_MAX_PER_LVL))
    return random.randint(*MON_DMG)
AGGRO_TIMEOUT = 20.0        # drop aggro this long after the player last engaged
MAX_RP = 100                # Determination/resource cap
DETERMINED_AT = 50          # at/above this you are "Determined" (tooltip) -> next skill empowered
DET_AUTO_GAIN = 5           # Determination an auto-attack grants
DET_SKILL_GAIN = 10         # Determination a (non-empowered) skill grants
EMPOWER_MULT = 2.0          # damage multiplier when a skill spends a Determined charge

_php = {}                   # uid -> current player HP
_rp = {}                    # uid -> current Determination (built by autos/skills, spent when Determined)
_power = {}                 # uid -> {ap, sp, tcr, scm}: stat-derived attack power for damage


# DamageType popup kinds (BattleTextBouncer.DamageType): the resolved Damage node's
# DamageTypes is the POPUP kind, not the element. 0 Normal, 1 Crit, 2 Dodge, 3 Miss, 5 DoT.
DT_NORMAL, DT_CRIT, DT_DODGE, DT_MISS, DT_DOT = 0, 1, 2, 3, 5
DODGE_CHANCE = 0.001        # target evasion (type 2) — vanishingly rare in capture (5/8841)

# Weapon-damage term (P1-2 fallback): with no equipped weapon gem, a hit rolls within
# [ap*WEAPON_MIN, ap*WEAPON_MAX] (sp for magical) * the skill multiplier. Tuned so an auto
# (mult 1) at ap 31 lands ~56-78 — the captured auto band.
WEAPON_MIN, WEAPON_MAX = 1.8, 2.5

# Gem weapon model (KEYSTONE, capture 2026-06-18): when a weapon gem IS equipped, damage =
# weaponRoll(Base+-Wild) * (1 + power*AP_DMG_COEF) * skillMult, crit ×scm. The weapon-gem range
# (e.g. 27-34) dominates; attack/spell power adds only a small multiplier. AP_DMG_COEF is pinned
# to the captured auto MODE (~1.15x at ap 24: weaponRoll 27-34 -> ~31-39) — a one-point fit, so
# the coefficient itself is OURS/flagged; the weapon range it multiplies is 1=1 (tooltip-exact).
AP_DMG_COEF = 0.00625


def set_power(uid, sta, weapon=None):
    """Record a player's combat stats (from build_combat_stats) so Damage scales with their
    attack power + crit + to-hit (tha) instead of a flat roll. `weapon` = the equipped weapon
    gem's (DmgMin, DmgMax) range (pattern_bonus); when set, _hit rolls IT instead of the
    attack-power fallback (keystone: gems are the damage source)."""
    if sta:
        _power[uid] = {"ap": float(sta.get("ap") or 0), "sp": float(sta.get("sp") or 0),
                       "tcr": float(sta.get("tcr") or 0.05), "scm": float(sta.get("scm") or 1.5),
                       "tha": float(sta.get("tha") or 1.0),
                       # Arcane Shield scales from the primary INT stat, not derived SP.
                       "INT": float(sta.get("INT") or 0)}
        if weapon:
            _power[uid]["weapon"] = (float(weapon[0]), float(weapon[1]))


def _hit(caster, mult, magical, always_hit=False):
    """Resolve one hit -> (dmg, dtype). dtype is the POPUP kind: 0 Normal, 1 Crit, 2 Dodge,
    3 Miss; a miss/dodge deals 0. The attacker's to-hit (`tha`) is rolled first (a miss is the
    attacker's accuracy failing — capture: ~1% for a player at tha 0.99); then a rare target
    dodge; then damage scales on ap/sp + crit. Falls back to the flat roll (Normal) for
    monsters / unregistered casters. `always_hit` skips the miss/dodge rolls (a Damage node
    authored Guaranteed — e.g. Paladin's Smite, which spends the whole pool and must land)."""
    uid = None
    if isinstance(caster, str) and caster.startswith("p:"):
        try:
            uid = int(caster.split(":")[1])
        except (ValueError, IndexError):
            uid = None
    p = _power.get(uid) if uid is not None else None
    if p is None:
        return _roll(mult), DT_NORMAL            # monster / no stats -> legacy roll, no crit
    if not always_hit and random.random() > p.get("tha", 1.0):
        return 0, DT_MISS                        # to-hit failed (Damages:[0], no HP change)
    if not always_hit and random.random() < DODGE_CHANCE:
        return 0, DT_DODGE                       # target evaded
    power = p["sp"] if magical else p["ap"]
    wr = p.get("weapon")
    if wr:                                       # keystone: roll the equipped weapon gem's range
        d = random.uniform(wr[0], wr[1]) * (1 + power * AP_DMG_COEF) * mult
    else:                                        # no gem -> attack-power-derived fallback (P1-2)
        d = random.uniform(power * WEAPON_MIN, power * WEAPON_MAX) * mult
    d *= 1.0 + _guard_dmg_bonus(caster)          # Paladin's Guard: outgoing damage up
    crit = random.random() < p["tcr"]
    if crit:
        d *= p["scm"]
    return max(1, round(d)), (DT_CRIT if crit else DT_NORMAL)


def _uid_of(target_string):
    """The integer uid in a 'p:<uid>' target string (or None)."""
    try:
        return int(str(target_string).split(":")[1])
    except (ValueError, IndexError):
        return None


def _heal_amount(caster, mult):
    """Positive heal amount for one target + crit flag. Heals scale on the caster's SPELL
    power (healers are casters) with the skill's authored Multiplier, crit via tcr->scm —
    the same shape as `_hit`, just additive. Falls back to the flat roll for unregistered
    casters. (Heal coefficients are OURS — AE's heal formula is server-internal and was never
    captured; the Multiplier is tuned so heals land in the captured ~120-650 band for sp~31.)"""
    uid = _uid_of(caster) if isinstance(caster, str) and caster.startswith("p:") else None
    p = _power.get(uid) if uid is not None else None
    if p is None:
        return _roll(mult), False
    amt = p["sp"] * mult * random.uniform(0.85, 1.15)
    crit = random.random() < p["tcr"]
    if crit:
        amt *= p["scm"]
    return max(1, round(amt)), crit


def _raise_hp(uid, amt):
    """Raise a player's tracked HP by amt (clamped to their max). Returns the new HP — the
    Damage node's TargetHP, which applies the heal client-side."""
    if uid is None:
        return amt
    mx = _pmax.get(uid, PLAYER_MAXHP)
    new = min(mx, _php.get(uid, mx) + amt)
    _php[uid] = new
    return new


def _is_heal(props):
    """True for a Damage node authored as a heal (negative-damage popup raising ally/self
    HP). The heal sign is authored, not minable — extract_skill_graphs forces every Damage
    offensive, so a healed skill carries an explicit `Heal` flag."""
    return bool(props.get("Heal"))


def _lifelink_node(skill_id, caster, total, allies):
    """Smite's holy lifelink (LIFELINK table): after the cast resolves, heal the caster +
    allies for a fraction of the damage it dealt. Returns a resolved heal Damage node
    (negative Damages -> green popups) or None. Immediate so it renders even after
    DispenseDamage, like the empowered self-heal."""
    pct = LIFELINK.get(skill_id)
    if not pct or total <= 0:
        return None
    amt = max(1, round(total * pct))
    tgts = _ally_targets(caster, allies)[:PALADIN_MAX_TARGETS]
    damages, hps = [], []
    for ts in tgts:
        damages.append(-amt)
        hps.append(_raise_hp(_uid_of(ts), amt))
    return {"Name": "Damage", "DamageTypes": [0] * len(tgts), "Damages": damages,
            "Targets": tgts, "TargetHPs": hps, "Immediate": True}


DET_GRANT = {105: 50}       # skills that GRANT a determination burst (Dragon's Bane: +50)
AUTO_MANA_REGEN = 10        # mana an auto-attack restores for a mana class (Magic Missile)
MAGE_MAGIC_MISSILE = 135
MAGE_FIREBALL = 136
MAGE_ICE_SHARD = 137
MAGE_EXPLOSION = 138
MAGE_ARCANE_SHIELD = 139
MAGE_SKILLS = {MAGE_MAGIC_MISSILE, MAGE_FIREBALL, MAGE_ICE_SHARD,
               MAGE_EXPLOSION, MAGE_ARCANE_SHIELD}
WARRIOR_ON_GUARD = 118

# Dragon's Bane (105), from its tooltip: "Passive: your skills deal 20% more damage to
# Dragonkin." Casting it applies Dragonbane for 10s -> +50% vs Dragonkin AND doubles
# Determination gain. (P2-3; monster race comes from register_monster, P1-3.)
DRAGONBANE_SECS = 10.0
DRAGON_PASSIVE_MULT = 1.20  # DS passive vs Dragonkin
DRAGON_BANE_MULT = 1.50     # while the Dragonbane buff is active
_dragonbane = {}            # uid -> time the Dragonbane buff ends (>now = active)
_dragonbane_shown = set()   # uids showing the Dragonbane aura -> need an AuraChange remove on expiry


def _dragonbane_active(uid):
    return time.time() < _dragonbane.get(uid, 0.0)


def expired_dragonbane():
    """uids whose Dragonbane buff has ended but still show its aura — they need a removal so the
    red glow clears. Returns them and clears the 'shown' flag so the AuraChange is sent once."""
    now = time.time()
    out = []
    for uid in list(_dragonbane_shown):
        if now >= _dragonbane.get(uid, 0.0):
            _dragonbane_shown.discard(uid)
            out.append(uid)
    return out


def aura_remove_packet(uid, name):
    """A standalone AuraChange that REMOVES an aura from a player (auraCmd 1 = Remove, per
    ResponseAuraChange.auraAction and the capture, where auraCmd:1 clears a target's auras)."""
    return {"Cmd": "AuraChange", "auraCmd": 1, "nam": name, "Target": f"p:{uid}",
            "casterTS": f"p:{uid}", "uniquenessType": 1, "Icon": ""}


def aura_remove_target_packet(target, name):
    """Generic AuraChange removal for non-player targets such as monsters."""
    return {"Cmd": "AuraChange", "auraCmd": 1, "nam": name, "Target": target,
            "casterTS": target, "uniquenessType": 1, "Icon": ""}


def _dragon_bonus(caster, area, mon_ts):
    """Dragon's Bane damage bonus vs a target. A Dragonslayer (determination model — Bane is
    DS-only) deals +20% to Dragonkin-race monsters, +50% while Dragonbane is active; 1.0 else."""
    uid = _uid_of(caster) if isinstance(caster, str) and caster.startswith("p:") else None
    if uid is None or _resource_model.get(uid) != "determination":
        return 1.0
    race = ((_moninfo.get((area, mon_ts)) or {}).get("race") or "").lower()
    if "dragon" not in race:                          # matches "Dragonkin"/"Dragon"
        return 1.0
    return DRAGON_BANE_MULT if _dragonbane_active(uid) else DRAGON_PASSIVE_MULT

# Per-class resource MODEL (set on login / class switch). Two models exist in the capture:
#   determination — DS: skills BUILD it, at 50 you're Determined -> next skill empowered (white
#                   bar, orange at the threshold).
#   mana          — every other class: skills SPEND their cost, autos restore (blue bar, no
#                   threshold). No generic Determined empower.
#   conviction    — Paladin (Reduxidain, class 69420, OURS): a stacking pool. The auto and Vow
#                   BUILD stacks, each stack multiplies the basic abilities (CONVICTION_SCALING),
#                   and Smite CONSUMES the whole pool for its payoff. Idle stacks decay
#                   (conviction_decay). Class MaxRP (50) is honored via _rp_max.
#   heroic        — the DATA-driven stacking pool (Infinity Hero, class 2022). Same shape as
#                   conviction except it does NOT decay: the captured AE sessions climb
#                   steadily to the 25 threshold across minutes of play with no drain, and
#                   the whole build/spend is authored in the class's rule config rather than
#                   keyed by skill_id here.
#   stacking      — generic data-authored pool (for example Chronomancer charges).
_resource_model = {}        # uid -> mana | determination | conviction | heroic | stacking
_class_mana = {}            # uid -> {skill_id: mana_cost} (for the mana model)
_rp_max = {}                # uid -> the class's resource cap (updateClass MaxRP; default 100)

STACK_MODELS = ("conviction", "heroic", "stacking")


def set_resource_model(uid, model, max_rp=MAX_RP):
    """Record a player's class resource model so casts build/spend the right pool. A mana
    class starts full; every build/spend pool starts empty."""
    model = model if model in ("mana", "determination", "conviction", "heroic", "stacking") \
        else "determination"
    _resource_model[uid] = model
    _rp_max[uid] = int(max_rp or MAX_RP)
    _rp[uid] = _rp_max[uid] if model == "mana" else 0


# --- the DATA path: classes whose mechanics come from a rule config --------------------
# A class carrying classes.raw["rules"] runs its casts through combat_engine instead of the
# skill_id-keyed Python in this module. Both paths share the same rolls, HP and pools
# (combat_engine/live.py bridges onto here), which is what let test_port_parity.py prove
# them identical cast-for-cast before this switchover.
_class_rules = {}           # uid -> the equipped class's rule config (absent = Python path)
_delayed = []               # [(due_ts, area, status, nodes)] async sends (the meteor's fire)


def set_class_rules(uid, rules):
    """Record the equipped class's rule config. None/empty puts the player back on the
    Python path, so this doubles as the per-class kill switch."""
    if rules:
        _class_rules[uid] = rules
    else:
        _class_rules.pop(uid, None)


def class_rules(uid, skill_id=None):
    """The caster's rule config, or None. With a skill_id, only when THAT skill is authored
    there — an unported slot on a ported class still falls through to the Python path."""
    cfg = _class_rules.get(uid)
    if not cfg:
        return None
    if skill_id is None:
        return cfg
    return cfg if str(skill_id) in (cfg.get("skills") or {}) else None


def queue_delayed(area, packets, now=None):
    """Schedule packets a cast asked to send LATER (the Packet/Delay rule node — the meteor's
    burning ground lands a second after the impact). The AI loop flushes due_delayed()."""
    base = now if now is not None else time.time()
    for delay_ms, status, nodes in packets or []:
        _delayed.append((base + delay_ms / 1000.0, area, status, nodes))


def due_delayed(now=None):
    """-> [(area, status, nodes)] whose delay has elapsed, removing them from the queue."""
    now = now if now is not None else time.time()
    if not _delayed:
        return []
    out = [(a, s, n) for due, a, s, n in _delayed if due <= now]
    if out:
        _delayed[:] = [row for row in _delayed if row[0] > now]
    return out


def set_class_mana(uid, costs):
    """Record the caster's class skill mana costs ({skill_id: cost}) for the mana model."""
    _class_mana[uid] = dict(costs or {})


def resource_model(uid):
    """The player's equipped resource model, or None before combat registration."""
    return _resource_model.get(uid)


def resource_total(uid):
    """Authoritative current RP/mana value used for change detection and resyncs."""
    if uid in _rp:
        return _rp[uid]
    return _rp_max.get(uid, MAX_RP) if _resource_model.get(uid) == "mana" else 0


def mana_cost(uid, skill_id):
    """The positive mana requirement for a skill (zero for non-mana classes)."""
    if _resource_model.get(uid) != "mana" or skill_id is None:
        return 0
    return max(0, int((_class_mana.get(uid) or {}).get(skill_id, 0) or 0))


def can_pay_resource(uid, slot, skill_id=None):
    """Return (allowed, required). Call before committing the cast cooldown."""
    cost = 0 if int(slot) == 0 else mana_cost(uid, skill_id)
    return resource_total(uid) >= cost, cost


class InsufficientMana(RuntimeError):
    def __init__(self, required, available):
        self.required = int(required)
        self.available = int(available)
        super().__init__(f"not enough mana: need {self.required}, have {self.available}")


def _apply_mana(uid, slot, skill_id=None):
    """Mana model: a skill SPENDS its cost, the auto-attack RESTORES mana.
    Returns (new_total, False) — mana classes have no generic Determined empower."""
    cap = _rp_max.get(uid, MAX_RP)
    cur = _rp.get(uid, cap)
    if slot == 0:                                   # auto-attack restores mana
        # Mage's passive doubles Magic Missile's restoration while the caster is
        # below 30 mana. Other mana-class autos retain the generic +10 model.
        gain = AUTO_MANA_REGEN * 2 if skill_id == MAGE_MAGIC_MISSILE and cur < 30 \
            else AUTO_MANA_REGEN
        cur = min(cap, cur + gain)
    else:                                           # skill spends its authored cost
        cost = mana_cost(uid, skill_id)
        if cur < cost:
            raise InsufficientMana(cost, cur)
        cur -= cost
    _rp[uid] = cur
    return (cur, False)


# --- Conviction (Paladin/Reduxidain class 69420, OURS) ------------------------
# Stacks build from the basic attacks, multiply the class's abilities per stack, and Smite
# consumes the whole pool. Out of combat (no cast for CONV_DECAY_IDLE) they drain.
CONV_AUTO_GAIN = 3                  # any slot-0 auto builds stacks (the sustained AI-loop
                                    # re-cast has no skill_id, so the auto keys on SLOT). +3 on
                                    # a 2s auto = a visible climb (+1 was imperceptible); DS
                                    # builds +5/auto on its 100 pool, this is ~the same pace on 50.
# The stacking-pool model is shared by BOTH stack classes — Paladin's "Conviction" and the
# Voidwalker's "Hunger" (class 2064, OURS) are the same machinery, keyed per skill_id below.
CONV_GAIN = {90369: 2,              # skill_id -> stacks gained on cast (Paladin's Vow +2)
             90381: 2,              # Voidwalker's Essence Siphon +2
             90382: 5}              # Voidwalker's Hungering Maw +5
CONV_SPENDERS = {90372,             # Paladin's Smite: consumes ALL stacks
                 90384}             # Voidwalker's Manifest Oblivion: consumes ALL stacks
CONVICTION_SCALING = {              # skill_id -> bonus multiplier per stack this cast
    90369: 0.03,                    # Vow: +3%/stack physical (up to +150% at 50)
    90372: 0.05,                    # Smite: +5%/stack magical, on the CONSUMED stacks
    90381: 0.02,                    # Essence Siphon: +2%/stack magical
    90382: 0.02,                    # Hungering Maw: +2%/stack magical
    90384: 0.05,                    # Manifest Oblivion: +5%/stack, on the CONSUMED stacks
}
LIFELINK = {90372: 0.30,            # Smite heals caster+allies for 30% of its damage
            90381: 0.35}            # Essence Siphon drinks 35% of its damage back
CONV_DECAY_IDLE = 10.0              # seconds without casting before stacks start draining
CONV_DECAY_RATE = 5                 # stacks lost per decay step
CONV_DECAY_STEP = 1.0               # seconds between decay steps once draining
_conv_cast_stacks = {}              # uid -> stacks empowering the in-flight cast
_conv_last_cast = {}                # uid -> last conviction cast ts (decay idle timer)
_conv_next_decay = {}               # uid -> next decay step ts


def _apply_conviction(uid, slot, skill_id=None):
    """Conviction model: record the stacks that empower THIS cast, then build (auto/Vow)
    or consume-all (Smite). Returns (new_total, False) — Conviction never uses the generic
    Determined empower; its scaling flows through _conviction_mult instead."""
    cap = _rp_max.get(uid, MAX_RP)
    cur = min(_rp.get(uid, 0), cap)
    _conv_last_cast[uid] = time.time()
    _conv_cast_stacks[uid] = cur                    # Vow/Protection scale on current stacks;
    if skill_id in CONV_SPENDERS:                   # Smite/Manifest scale on what they consume
        _rp[uid] = 0
        return (0, False)
    gain = CONV_AUTO_GAIN if slot == 0 else CONV_GAIN.get(skill_id, 0)
    cur = min(cap, cur + gain)
    _rp[uid] = cur
    return (cur, False)


def _conviction_mult(uid, skill_id):
    """This cast's Conviction damage/heal multiplier (1.0 for non-conviction casters and
    unscaled skills). Consumes the stacks recorded by _apply_conviction."""
    if uid is None or _resource_model.get(uid) != "conviction":
        return 1.0
    stacks = _conv_cast_stacks.pop(uid, 0)
    per = CONVICTION_SCALING.get(skill_id)
    return 1.0 + per * stacks if per else 1.0


def conviction_decay():
    """Out-of-combat Conviction drain -> [(uid, new_total)] for the AI loop to push to
    clients (resource_packet). After CONV_DECAY_IDLE without a cast, a conviction player
    loses CONV_DECAY_RATE stacks every CONV_DECAY_STEP seconds until empty."""
    now = time.time()
    out = []
    for uid, model in list(_resource_model.items()):
        if model != "conviction":       # NOT heroic: that pool holds until it is spent
            continue
        cur = _rp.get(uid, 0)
        if cur <= 0 or now - _conv_last_cast.get(uid, 0.0) < CONV_DECAY_IDLE:
            continue
        if now < _conv_next_decay.get(uid, 0.0):
            continue
        _conv_next_decay[uid] = now + CONV_DECAY_STEP
        cur = max(0, cur - CONV_DECAY_RATE)
        _rp[uid] = cur
        out.append((uid, cur))
    return out


def resource_packet(uid, name):
    """hpmp that re-syncs a player's CURRENT HP/RP without changing them (bar-only updates,
    e.g. Conviction decay). Same shape/lowercase-name rule as rest_player."""
    return {"Cmd": "hpmp", "unm": (name or "").lower(),
            "HP": _php.get(uid, _pmax.get(uid, PLAYER_MAXHP)), "RP": _rp.get(uid, 0),
            "State": 1}


def cast_failure(uid, slot, error=""):
    """Terminal failed Attack response that releases the client's pending skill.

    StatusCode 0 is ResponseAttack.NodeStatusCode.Fail. An empty error is used
    for harmless duplicate/cooldown requests so no red combat message appears.
    """
    return {"Cmd": "Attack", "Caster": f"p:{uid}", "Slot": int(slot),
            "StatusCode": 0, "Wait": False, "Error": str(error or ""), "Nodes": []}


def _apply_determination(uid, slot, skill_id=None):
    """Resolve a player's resource for one cast (model-aware). For the mana model, spend/
    restore; for Determination, build until 50 then the next skill spends+empowers.
    Returns (total_after, empowered) — total_after is what the cast's Resource node reports."""
    if uid is None or slot not in (0, 1, 2, 3, 4):
        # Slot 5/-1 are spellstones or external actions, not class skills;
        # they must never build/spend the equipped class's RP or mana.
        return (None, False)
    if _resource_model.get(uid) == "mana":          # mana classes spend, don't build/empower
        return _apply_mana(uid, slot, skill_id)
    if _resource_model.get(uid) == "conviction":    # Paladin: stack build/consume-all
        return _apply_conviction(uid, slot, skill_id)
    cur = _rp.get(uid, 0)
    grant = DET_GRANT.get(skill_id)
    if grant is not None:                           # ultimate: grants Determination
        cur = min(MAX_RP, cur + grant)
        _rp[uid] = cur
        if skill_id == 105:                         # Dragon's Bane -> Dragonbane buff (P2-3)
            _dragonbane[uid] = time.time() + DRAGONBANE_SECS
            _dragonbane_shown.add(uid)              # mark the aura so it's removed on expiry
        return (cur, False)
    if slot != 0 and cur >= DETERMINED_AT:          # Determined -> consume + empower
        _rp[uid] = 0
        return (0, True)
    gain = DET_AUTO_GAIN if slot == 0 else DET_SKILL_GAIN
    if _dragonbane_active(uid):                      # Dragonbane doubles Determination gain
        gain *= 2
    cur = min(MAX_RP, cur + gain)
    _rp[uid] = cur
    return (cur, False)


_pmax = {}                  # uid -> max HP (stat-derived; for revive)


# Per-skill "Determined" empowerments (the tooltip effects). When a skill is cast while
# Determined, instead of a flat 2x it gets its real effect: a damage multiplier and/or a
# post-cast effect (self-heal, target stun). Skills not listed fall back to EMPOWER_MULT.
EMPOWERED_FX = {
    167: {"kind": "multistrike", "hits": 3},          # Scorched Steel: strike 3 times
    103: {"kind": "heal", "pct": 0.15},               # Impale: heal 15% of max HP
    104: {"kind": "stun", "secs": 3.0},               # Incapacitate: 3s stun
}

_stun = {}                  # (area, "m:ID") -> time the stun ends (monster can't act)


def _empower(skill_id, empowered):
    """(damage_mult, post_effect, hits) for a cast. post_effect in {None,'heal','stun'};
    a heal/stun deals no bonus damage (the effect IS the payoff). `hits` > 1 means the
    Damage node fires that many SEPARATE times (Scorched's triple-strike), as AE does."""
    if not empowered:
        return 1.0, None, 1
    fx = EMPOWERED_FX.get(skill_id)
    if not fx:
        return EMPOWER_MULT, None, 1
    if fx["kind"] == "multistrike":
        return 1.0, None, int(fx["hits"])           # N independent full-damage hits
    return 1.0, fx["kind"], 1


def _empower_node(skill_id, uid, caster, area, targets):
    """Apply + return the extra resolved node for an empowered heal/stun (or None)."""
    fx = EMPOWERED_FX.get(skill_id) or {}
    if fx.get("kind") == "heal":
        mx = _pmax.get(uid, PLAYER_MAXHP)
        heal = round(fx["pct"] * mx)
        new = min(mx, _php.get(uid, mx) + heal)
        _php[uid] = new
        # The client shows a heal as a NEGATIVE-damage popup (BattleTextBouncer: HP<0 ->
        # green popupHeal); DamageTypes is the popup kind (0=Normal), NOT the element.
        # Immediate so it renders even though it's appended after the skill's DispenseDamage.
        return {"Name": "Damage", "DamageTypes": [0], "Damages": [-heal],
                "Targets": [caster], "TargetHPs": [new], "Immediate": True}
    if fx.get("kind") == "stun":
        mons = [t for t in targets if isinstance(t, str) and t.startswith("m:")]
        for ts in mons:
            _stun[(area, ts)] = time.time() + fx["secs"]
        if mons:
            # the on-target stun VISUAL is a "Stunned" aura (the Restrict node only locks the
            # CASTER, not the target — confirmed from the capture).
            return {"Name": "Aura", "Hide": False, "Animation": "", "AuraName": "Stunned",
                    "Targets": mons, "casterTS": caster, "uniquenessType": 0}
    return None


def _mage_prepare_node(area, caster, targets, props, skill_id, side_packets):
    """Apply Mage tooltip interactions around one legacy graph node.

    The captured graph describes presentation and node order, but not conditional
    server logic. This hook keeps those conditions server-authoritative while
    continuing to render the original graph through the normal combat path.
    """
    if skill_id not in MAGE_SKILLS:
        return props, targets
    props = dict(props)
    targets = list(dict.fromkeys(targets or []))
    monsters = [t for t in targets if valid_combat_target(area, t)]
    name = props.get("Name")

    def consume(target, aura_name):
        if consume_aura(area, target, aura_name):
            if side_packets is not None:
                side_packets.append(aura_remove_target_packet(target, aura_name))
            return True
        return False

    if name == "Damage":
        if skill_id == MAGE_FIREBALL:
            # Fireball consumes Frozen Blood for double damage. It is a
            # single-target skill, so one multiplier applies to the whole node.
            if monsters and aura_active(area, monsters[0], "Frozen Blood"):
                props["Multiplier"] = float(props.get("Multiplier") or 1.0) * 2.0
                consume(monsters[0], "Frozen Blood")
        elif skill_id == MAGE_ICE_SHARD:
            for target in monsters:
                consume(target, "Scorched")
        elif skill_id == MAGE_EXPLOSION:
            for target in monsters:
                if detonate_scorched(area, target, caster) and side_packets is not None:
                    side_packets.append(aura_remove_target_packet(target, "Scorched"))

    if name == "Aura":
        aura_name = props.get("AuraName")
        if skill_id == MAGE_ICE_SHARD and aura_name == "Stunned":
            # Freeze Immune is a per-life marker: only the first Ice Shard
            # against this monster applies the three-second stun.
            targets = [t for t in monsters if not aura_active(area, t, "Freeze Immune")]
        elif skill_id == MAGE_EXPLOSION and aura_name == "Shattered":
            # Shattered is conditional and consumes Frozen Blood.
            targets = [t for t in monsters if aura_active(area, t, "Frozen Blood")]
            for target in targets:
                consume(target, "Frozen Blood")
        elif skill_id in (MAGE_FIREBALL, MAGE_ICE_SHARD, MAGE_EXPLOSION):
            targets = monsters
        if not targets:
            return None, []
    return props, targets


def _render_nodes(area, slot, caster, targets, nodes, props, det_total, empower_mult, hits,
                  allies=None, skill_id=None, side_packets=None):
    """Wrap _render_node: a Damage node with hits>1 is emitted as that many SEPARATE hits
    (each rolls its own damage) so a triple-strike shows three numbers, not one big one."""
    props, targets = _mage_prepare_node(
        area, caster, targets, props, skill_id, side_packets)
    if props is None:
        return [], 0, []
    if skill_id == WARRIOR_ON_GUARD and props.get("Name") == "Cooldown":
        # NodeCooldown sets pendingCooldown=True when Animation is non-empty and waits for that
        # exact animation callback before starting the ring. The shipped On Guard graph names a
        # callback that does not fire on the current Warrior rig, leaving slot 4 pending forever
        # until an area transition resets the UI. Start this cooldown immediately; the server's
        # authoritative 14.799s gate still prevents early recasts.
        props = dict(props)
        props["Animation"] = ""
    node, dmg, killed = _render_node(area, slot, caster, targets, nodes, props,
                                     det_total, empower_mult, allies)
    if node is None:
        return [], 0, []
    if props.get("Name") == "Damage" and hits > 1:
        out, total, allk = [node], dmg, list(killed)
        for _ in range(hits - 1):
            n2, d2, k2 = _render_node(area, slot, caster, targets, nodes, props,
                                      det_total, empower_mult, allies)
            out.append(n2); total += d2; allk += k2
        return out, total, allk
    return [node], dmg, killed


def is_stunned(area, mon_ts):
    return time.time() < _stun.get((area, mon_ts), 0.0)


# --- Auras: tooltip-grounded debuff effects + DoT/HoT ticks (P2-4) ------------
# Aura NAMES + durations are real (capture: Bleeding 236, Weakened 167, Scorched 27, Radiance
# 112, Inhibition 184, ...; durations from the tooltips). DEBUFF damage effects are tooltip-
# grounded (Weakened/Inhibition: target deals -10%). DoT/HoT TICKS are a DESIGN mechanic:
# DamageType 5 (DoT) NEVER appears in the capture (0 / 48k packets), so that type-5 is used AND
# the tick amounts are OURS, not 1=1 — tick = a fraction of the caster's power (flagged).
DOT_INTERVAL = 1.0          # seconds between ticks
WEAKEN_MULT = 0.90          # Weakened/Inhibition: the target deals 10% less (tooltip)

AURA_FX = {
    "Bleeding":   {"kind": "dot", "secs": 3, "tick": 0.30, "magical": False},   # Impale
    "Scorched":   {"kind": "dot", "secs": 6, "tick": 0.25, "magical": True},    # Fireball
    # Mage status chain. Freeze Immune is a per-monster-life marker and is
    # cleared with the rest of that monster's auras on death/respawn.
    "Frozen Blood": {"kind": "slow", "secs": 6, "speed": 0.90},
    "Stunned":      {"kind": "stun", "secs": 3, "speed": 0.0},
    "Freeze Immune": {"kind": "marker", "secs": 86400},
    "Shattered":    {"kind": "physicaldebuff", "secs": 4, "mult": 0.90},
    "Radiance":   {"kind": "hot", "secs": 5, "tick": 0.20},                     # Healing Word
    "Weakened":   {"kind": "dmgdebuff", "secs": 5},                             # Incapacitate
    "Inhibition": {"kind": "dmgdebuff", "secs": 8},                             # Energy Flow
    # InfinityHero Meteor: Warrior leaves a 5s INT/WIS-scaled burning crater; without
    # positional ground-zone tracking we attach its magical ticks to the struck monster.
    # Healer applies the tooltip's 6s -10% outgoing damage debuff (monster attacks do not
    # currently crit, so the Crit Chance clause is retained in the client tooltip only).
    "Burning Field": {"kind": "dot", "secs": 5, "tick": 0.25, "magical": True},
    "Suppression":   {"kind": "dmgdebuff", "secs": 6},
    # Paladin's Guard (OURS): -25% incoming damage + outgoing damage up for the buffed —
    # the outgoing bonus scales with the caster's Conviction AT CAST (+0.2%/stack).
    "Paladin's Guard": {"kind": "guard", "secs": 6, "dr": 0.25,
                        "dmg_base": 0.10, "dmg_per_stack": 0.002},
    # Tooltip formula: 20 percentage points plus 5% of the caster's INT
    # contribution (INT 100 -> +5 percentage points). Each landed hit costs 5 mana.
    "Arcane Shield": {"kind": "guard", "secs": 6, "dr": 0.20,
                       "int_dr": 0.0005, "mana_per_hit": 5, "self_only": True,
                       "dmg_base": 0.0, "dmg_per_stack": 0.0},
    # Warrior On Guard: the shipped tooltip promises 50% Damage Resistance for five seconds.
    "On Guard":      {"kind": "guard", "secs": 5, "dr": 0.50, "self_only": True,
                       "dmg_base": 0.0, "dmg_per_stack": 0.0},
    # Voidwalker (class 2064, OURS): Hungering Maw's gnawing DoT + Event Horizon, the domain
    # buff (same guard machinery as Paladin's Guard, scaling on the caster's Hunger at cast).
    "Umbral Rot":    {"kind": "dot", "secs": 5, "tick": 0.25, "magical": True},
    "Event Horizon": {"kind": "guard", "secs": 6, "dr": 0.20,
                      "dmg_base": 0.10, "dmg_per_stack": 0.002},
    # Lethal Abomination's Shadow Form (transform: True) — while morphed into the abomination
    # you hit 25% harder and take 15% less; when it expires, aura_ticks sends the detransform.
    # (dmg_per_stack 0: the spender zeroes the pool before the aura snapshots, so scaling per
    # stack would always read 0 — the payoff for stacks is the nuke, the form is flat.)
    "Shadow Form":   {"kind": "guard", "secs": 8, "dr": 0.15,
                      "dmg_base": 0.25, "dmg_per_stack": 0.0, "transform": True},
    # Practice Spellstone: the aura exists only to own the transformation lifetime. It has no
    # combat bonuses, but uses the same transform-expiry path as Shadow Form to restore avatar.
    "Practice Frogzard Form": {"kind": "guard", "secs": 30, "dr": 0.0,
                                "dmg_base": 0.0, "dmg_per_stack": 0.0,
                                "transform": True},
    # Chronomancer: server-authoritative monster pacing. `speed` is the share
    # of normal attack/cast speed; the loader mirrors it on the target's
    # Animators using metadata emitted by combat_engine.rules.
    "Time Dilation":   {"kind": "slow", "secs": 6.0, "speed": 0.35},
    "Temporal Stasis": {"kind": "stun", "secs": 2.5, "speed": 0.0},
}
_auras = {}                 # (area, ts) -> {name: {ends, next, amt, caster, kind}}


def aura_entry(area, target, name):
    """Active stored aura entry, or None when absent/expired."""
    entry = (_auras.get((area, target)) or {}).get(name)
    return entry if entry and time.time() < entry.get("ends", 0.0) else None


def aura_active(area, target, name):
    return aura_entry(area, target, name) is not None


def consume_aura(area, target, name):
    """Remove one server aura immediately; return its former entry when active."""
    auras = _auras.get((area, target)) or {}
    entry = auras.get(name)
    if not entry or time.time() >= entry.get("ends", 0.0):
        return None
    del auras[name]
    if name == "Stunned":
        _stun.pop((area, target), None)
    if not auras:
        _auras.pop((area, target), None)
    return entry


def detonate_scorched(area, target, caster):
    """Consume Scorched and compress its remaining DoT into two ticks over one second."""
    scorched = consume_aura(area, target, "Scorched")
    if not scorched:
        return False
    now = time.time()
    remaining_ticks = max(1, math.ceil(max(0.0, scorched["ends"] - now) / DOT_INTERVAL))
    total = max(1, int(scorched.get("amt", 0) or 0)) * remaining_ticks
    _auras.setdefault((area, target), {})["Detonation"] = {
        "ends": now + 1.01, "next": now + 0.5, "interval": 0.5,
        "amt": max(1, math.ceil(total / 2)), "ticks_left": 2,
        "caster": caster, "kind": "dot",
    }
    return True


def apply_aura(area, name, targets, caster):
    """Register a known aura's server-side effect on its targets — a DoT/HoT tick or a damage
    debuff. Unknown auras stay cosmetic. Tick amount scales on the caster's power (design)."""
    fx = AURA_FX.get(name)
    if not fx:
        return
    now = time.time()
    uid = _uid_of(caster) if isinstance(caster, str) and caster.startswith("p:") else None
    p = _power.get(uid) if uid is not None else None
    amt = 0
    if fx["kind"] in ("dot", "hot") and p:
        base = p["sp"] if (fx["kind"] == "hot" or fx.get("magical")) else p["ap"]
        amt = max(1, round(base * fx["tick"]))
    for t in targets:
        if not isinstance(t, str) or ":" not in t:
            continue
        if fx["kind"] == "dot" and not t.startswith("m:"):
            continue                                    # DoT lands on monsters only
        if fx["kind"] in ("hot", "guard") and not t.startswith("p:"):
            continue                                    # HoT/Guard on players only
        if fx["kind"] in ("slow", "stun", "marker", "physicaldebuff") \
                and not t.startswith("m:"):
            continue                                    # hostile Mage effects land on monsters only
        entry = {"ends": now + fx["secs"], "next": now + DOT_INTERVAL, "amt": amt,
                 "caster": caster, "kind": fx["kind"]}
        if fx["kind"] in ("slow", "stun"):
            entry["speed"] = float(fx.get("speed", 1.0))
        if fx["kind"] == "stun":
            _stun[(area, t)] = max(_stun.get((area, t), 0.0), entry["ends"])
        if fx["kind"] == "physicaldebuff":
            entry["mult"] = float(fx.get("mult", 1.0))
        if fx["kind"] == "guard":                       # snapshot the buff's strength at cast
            stacks = _rp.get(uid, 0) if _resource_model.get(uid) == "conviction" else 0
            entry["dr"] = min(0.75, fx["dr"] + fx.get("int_dr", 0.0) *
                              float((p or {}).get("INT", 0.0)))
            entry["dmg"] = fx["dmg_base"] + fx["dmg_per_stack"] * stacks
            if fx.get("mana_per_hit"):
                entry["mana_per_hit"] = int(fx["mana_per_hit"])
            if fx.get("transform"):
                entry["transform"] = True               # expiry must send the detransform
        _auras.setdefault((area, t), {})[name] = entry


def is_dmg_debuffed(area, ts):
    """Whether a target has an active damage debuff (Weakened/Inhibition/Suppression)."""
    now = time.time()
    return any(a["kind"] == "dmgdebuff" and now < a["ends"]
               for a in (_auras.get((area, ts)) or {}).values())


def outgoing_damage_multiplier(area, ts, physical=True):
    """Combined active outgoing-damage modifiers for an attacking monster."""
    now = time.time()
    mult = 1.0
    for aura in (_auras.get((area, ts)) or {}).values():
        if now >= aura["ends"]:
            continue
        if aura["kind"] == "dmgdebuff":
            mult *= WEAKEN_MULT
        elif physical and aura["kind"] == "physicaldebuff":
            mult *= float(aura.get("mult", 1.0))
    return mult


def monster_speed_multiplier(area, mon_ts):
    """Active share of normal monster action speed (1 normal, 0 frozen)."""
    now = time.time()
    speeds = [a.get("speed", 1.0) for a in (_auras.get((area, mon_ts)) or {}).values()
              if a["kind"] in ("slow", "stun") and now < a["ends"]]
    return max(0.0, min(speeds, default=1.0))


def monster_attack_interval(area, mon_ts):
    """Wall-clock delay between swings after active time-control effects."""
    speed = monster_speed_multiplier(area, mon_ts)
    return float("inf") if speed <= 0 else MON_ATTACK_CD / speed


def _guard_reduction(area, ts):
    """Incoming-damage reduction (0..1) from an active guard aura on this target."""
    now = time.time()
    return max((a.get("dr", 0.0) for a in (_auras.get((area, ts)) or {}).values()
                if a["kind"] == "guard" and now < a["ends"]), default=0.0)


def _drain_arcane_shield(area, uid, damage):
    """Charge Arcane Shield's five-mana landed-hit cost; expire it at zero mana."""
    if damage <= 0 or _resource_model.get(uid) != "mana":
        return False
    entry = aura_entry(area, f"p:{uid}", "Arcane Shield")
    if not entry:
        return False
    cost = int(entry.get("mana_per_hit", 0) or 0)
    if cost <= 0:
        return False
    _rp[uid] = max(0, _rp.get(uid, _rp_max.get(uid, MAX_RP)) - cost)
    if _rp[uid] == 0:
        entry["ends"] = time.time()        # aura_ticks emits the visual removal
    return True


def _guard_dmg_bonus(ts):
    """Outgoing-damage bonus (0..) from an active guard aura on this entity. Keyed only by
    target string (a player is in one area at a time), so _hit can use it without an area."""
    now = time.time()
    for (_a, t), auras in _auras.items():
        if t != ts:
            continue
        for a in auras.values():
            if a["kind"] == "guard" and now < a["ends"]:
                return a.get("dmg", 0.0)
    return 0.0


def aura_ticks():
    """Due DoT/HoT ticks across all areas -> [(area, attack_packet, killed_list)]. Expired auras
    are dropped. The server broadcasts these + handles kills. (DoT type-5 is a design mechanic —
    not in the capture.)"""
    now = time.time()
    out = []
    for key, auras in list(_auras.items()):
        area, ts = key
        for name, a in list(auras.items()):
            # A short detonation is allowed to finish both authored ticks even
            # if one AI-loop pass arrives slightly after its one-second window.
            if now >= a["ends"] and not (a["kind"] == "dot" and a.get("ticks_left", 0) > 0):
                del auras[name]
                if a["kind"] == "guard" and ts.startswith("p:"):
                    # like Dragonbane, a player buff's visual lingers without an explicit
                    # AuraChange remove — piggyback one on the tick broadcast
                    out.append((area, aura_remove_packet(_uid_of(ts), name), []))
                    if a.get("transform"):
                        # Shadow Form over — morph the player back (NodeMonTransform revert)
                        out.append((area, {"Cmd": "Attack", "Caster": ts, "Slot": -1,
                                           "StatusCode": 1, "Wait": False, "Error": "",
                                           "Nodes": [{"Name": "MonTransform",
                                                      "detransform": True}]}, []))
                elif a["kind"] in ("slow", "stun", "marker", "physicaldebuff") \
                        and ts.startswith("m:"):
                    out.append((area, aura_remove_target_packet(ts, name), []))
                continue
            if a["kind"] not in ("dot", "hot") or now < a["next"] or a["amt"] <= 0:
                continue
            a["next"] = now + float(a.get("interval", DOT_INTERVAL))
            if a.get("ticks_left") is not None:
                a["ticks_left"] = max(0, int(a["ticks_left"]) - 1)
                if a["ticks_left"] == 0:
                    a["ends"] = now
            if a["kind"] == "dot" and ts.startswith("m:"):
                prev = _mon.get(key, DEFAULT_HP)
                hp = max(0, prev - a["amt"])
                _mon[key] = hp
                node = {"Name": "Damage", "DamageTypes": [DT_DOT], "Damages": [a["amt"]],
                        "Targets": [ts], "TargetHPs": [hp]}
                killed = [ts] if (prev > 0 and hp <= 0) else []
            elif a["kind"] == "hot" and ts.startswith("p:"):
                new = _raise_hp(_uid_of(ts), a["amt"])
                node = {"Name": "Damage", "DamageTypes": [DT_DOT], "Damages": [-a["amt"]],
                        "Targets": [ts], "TargetHPs": [new]}
                killed = []
            else:
                continue
            out.append((area, {"Cmd": "Attack", "Caster": a["caster"], "Slot": -1,
                               "StatusCode": 1, "Wait": False, "Error": "", "Nodes": [node]},
                        killed))
        if not auras:
            _auras.pop(key, None)
    return out


def drop_auras_for(ts):
    """Clear auras whose target is this entity (a player leaving / a monster despawning)."""
    for key in list(_auras.keys()):
        if key[1] == ts:
            _auras.pop(key, None)
    for key in list(_stun.keys()):
        if key[1] == ts:
            _stun.pop(key, None)


# --- player-cast AoE damage zones (the Infinity Hero's Heroic Empowerment sky-blade,
# Meteor's Warrior-aspect burning field, and anything future authored the same way) ----
# A PlayerHitStream node only DECLARES the zone to the client -- exactly like a monster's
# tile skill, the ACTUAL hit detection is CLIENT-AUTHORITATIVE: the client reports who it
# caught via a `pmah` c2s command (RequestPlayerMonHit), Params = ["PlayerHitStream:<slot>",
# *monster_targets], repeatedly over the zone's lifetime (confirmed live: one Infinity Hero
# ult produced 455 separate pmah reports; one Meteor burning field produced ~2957 across a
# session -- this is a client-side per-frame/per-tick recheck, not a handful of authored
# ticks, so results MUST be throttled per (caster, slot, monster) the same way monster tile
# hits are throttled for the reverse direction). We register the active window (so a stale
# or spoofed report after it expires is ignored) when the PlayerHitStream node renders, and
# apply damage only in response to the client's own report -- no server-side blind ticking.
_hitstreams = {}                 # (uid, slot) -> {"area","end","mult","magical"}
_hitstream_last = {}             # (area, uid, slot, mon_ts) -> last applied ts (throttle)
HITSTREAM_HIT_THROTTLE = 0.5     # seconds: one damage application per target per window


def start_hitstream(area, uid, slot, duration_s, multiplier=1.5, magical=True):
    """Register one active AoE zone so a later `pmah` report from this caster/slot is
    honored (and a stale one after it ends is not)."""
    _hitstreams[(uid, slot)] = {"area": area, "end": time.time() + float(duration_s),
                                "mult": float(multiplier), "magical": bool(magical)}


def player_hitstream_hit(uid, slot, mon_targets):
    """Apply a client-reported PlayerHitStream hit (pmah) -> (attack_packet, killed_list),
    or (None, []) if there's no active window for this caster/slot (expired/stale report)
    or every listed target is still inside its throttle window."""
    st = _hitstreams.get((uid, slot))
    if st is None or time.time() >= st["end"]:
        return None, []
    area = st["area"]
    now = time.time()
    landed = []
    for ts in mon_targets:
        if not (isinstance(ts, str) and ts.startswith("m:")):
            continue
        k = (area, uid, slot, ts)
        if now - _hitstream_last.get(k, 0.0) < HITSTREAM_HIT_THROTTLE:
            continue
        _hitstream_last[k] = now
        landed.append(ts)
    if not landed:
        return None, []
    caster = f"p:{uid}"
    dtypes, damages, hps, killed = [], [], [], []
    for ts in landed:
        d, dtype = _hit(caster, st["mult"], st["magical"])
        key = (area, ts)
        prev = _mon.get(key, DEFAULT_HP)
        hp = max(0, prev - d)
        _mon[key] = hp
        dtypes.append(dtype); damages.append(d); hps.append(hp)
        if prev > 0 and hp <= 0:
            killed.append(ts)
    node = {"Name": "InstantDamage", "DamageTypes": dtypes, "Damages": damages,
            "Targets": landed, "TargetHPs": hps}
    attack = {"Cmd": "Attack", "Caster": caster, "Slot": -1, "StatusCode": 1,
             "Wait": False, "Error": "", "Nodes": [node]}
    return attack, killed


def monster_alive(area, mon_ts):
    return _mon.get((area, mon_ts), DEFAULT_HP) > 0


def valid_combat_target(area, mon_ts):
    """Whether a target is a registered, living monster in this exact area instance."""
    return bool(mon_ts and str(mon_ts).startswith("m:")
                and (area, str(mon_ts)) in _mon and _mon[(area, str(mon_ts))] > 0)


def active_combat_target(uid, area):
    """The player's current live monster, preferring their sustained-auto target."""
    auto = _auto.get(uid)
    if auto and auto.get("area") == area and valid_combat_target(area, auto.get("target")):
        return auto["target"]
    candidates = [(info.get("last", 0.0), mon)
                  for (aggro_area, mon), info in _aggro.items()
                  if aggro_area == area and info.get("uid") == uid
                  and valid_combat_target(area, mon)]
    return max(candidates, default=(0.0, None))[1]


def _target_sort_key(target):
    """Stable tie breaker for target strings, including summon IDs such as ``m:-1``."""
    try:
        return (0, int(str(target).split(":", 1)[1]))
    except (IndexError, ValueError):
        return (1, str(target))


def nearest_combat_target(area, frame=None, x=None, y=None):
    """Nearest living monster registered in a player's current cell.

    A CellJoin supplies authored monster coordinates while player movement keeps the member's
    current position. A monster without coordinates remains a valid fallback, but sorts after
    positioned monsters and ties are deterministic. No target is ever chosen from another cell.
    """
    try:
        px, py = float(x), float(y)
    except (TypeError, ValueError):
        px = py = None
    candidates = []
    for (candidate_area, target), hp in _mon.items():
        if candidate_area != area or hp <= 0:
            continue
        info = _moninfo.get((candidate_area, target), {})
        if frame is not None and info.get("frame") != frame:
            continue
        try:
            distance_sq = ((info["x"] - px) ** 2 + (info["y"] - py) ** 2)
            distance_key = (0, distance_sq)
        except (KeyError, TypeError):
            distance_key = (1, 0.0)
        candidates.append((distance_key, _target_sort_key(target), target))
    return min(candidates, default=(None, None, None))[2]


def resolve_combat_target(area, uid, requested=None, frame=None, x=None, y=None):
    """Prefer an explicit or active target; otherwise select the nearest living same-cell mob."""
    if valid_combat_target(area, requested):
        return str(requested)
    active = active_combat_target(uid, area)
    return active or nearest_combat_target(area, frame, x, y)


def in_combat(uid, area=None):
    """True while the player has a live target in the supplied (or any) area."""
    if area is not None:
        return active_combat_target(uid, area) is not None
    areas = {a.get("area") for a in _auto.values() if a}
    areas.update(a for a, _mon_ts in _aggro)
    return any(a and active_combat_target(uid, a) is not None for a in areas)


def register_player(uid, hp=None):
    mx = int(hp or PLAYER_MAXHP)
    _pmax[uid] = mx
    _php[uid] = mx
    _rp.setdefault(uid, 0)


def set_maxhp(uid, maxhp):
    """Update a player's max HP (e.g. after a helm-gem change) WITHOUT healing them: current
    HP is preserved, only re-clamped to the new max. register_player full-heals; this doesn't."""
    mx = int(maxhp)
    _pmax[uid] = mx
    if uid in _php:
        _php[uid] = min(_php[uid], mx)


def forget_player(uid):
    cancel_player_actions(uid, clear_cooldowns=True)
    _php.pop(uid, None)
    _pmax.pop(uid, None)
    _power.pop(uid, None)
    _rp.pop(uid, None)
    _resource_model.pop(uid, None)
    _class_mana.pop(uid, None)
    _rp_max.pop(uid, None)
    _active_aspect.pop(uid, None)
    _conv_cast_stacks.pop(uid, None)
    _conv_last_cast.pop(uid, None)
    _conv_next_decay.pop(uid, None)
    _class_rules.pop(uid, None)
    _dragonbane.pop(uid, None)
    _dragonbane_shown.discard(uid)
    caster = f"p:{uid}"
    try:
        from combat_engine.state import drop_state
        drop_state(caster)
    except Exception:
        pass
    drop_auras_for(f"p:{uid}")              # clear HoT/auras targeting this player


def player_hp(uid):
    return _php.get(uid, PLAYER_MAXHP)


def kill_monster(area, target):
    """Force a monster to 0 HP (/kill). Returns True if it was alive (so the caller credits
    the kill exactly once); the normal death/respawn flow takes it from here."""
    key = (area, target)
    prev = _mon.get(key, DEFAULT_HP)
    _mon[key] = 0
    return prev > 0


def engage(area, mon_ts, uid):
    """A player attacked a monster -> it aggros them and will keep swinging."""
    if not valid_combat_target(area, mon_ts):
        return
    _aggro[(area, mon_ts)] = {"uid": uid, "last": time.time()}


def disengage(area, mon_ts):
    _aggro.pop((area, mon_ts), None)
    _mon_skill.pop((area, mon_ts), None)
    _summoned.pop((area, mon_ts), None)        # stop tracking this boss's adds (clones persist)
    for k in [k for k in _mon_tile_last if k[0] == area and k[1] == mon_ts]:
        _mon_tile_last.pop(k, None)


def engagements():
    """Snapshot of (area, mon_ts, uid) the AI loop should drive this tick. Drops
    stale aggro (player long gone) and dead monsters."""
    now = time.time()
    out = []
    for (area, mon_ts), info in list(_aggro.items()):
        if _mon.get((area, mon_ts), 0) <= 0 or now - info["last"] > AGGRO_TIMEOUT:
            _aggro.pop((area, mon_ts), None)
            _mon_skill.pop((area, mon_ts), None)
            continue
        out.append((area, mon_ts, info["uid"]))
    return out


def monster_attack(area, mon_ts, uid):
    """One monster swing at the player. Returns (attack_packet, player_hp, player_died).
    Always swings (the AI loop owns pacing via MON_ATTACK_CD). Rolls a Miss (the monster's
    accuracy failing) or a rare player Dodge -> a 0-damage MISS/DODGE popup over the player."""
    if random.random() < MON_MISS_CHANCE:
        dmg, dtype = 0, DT_MISS
    elif random.random() < DODGE_CHANCE:
        dmg, dtype = 0, DT_DODGE
    else:
        dmg, dtype = _monster_dmg(area, mon_ts), DT_NORMAL
        out_mult = outgoing_damage_multiplier(area, mon_ts, physical=True)
        if out_mult != 1.0:
            dmg = max(1, round(dmg * out_mult))
        red = _guard_reduction(area, f"p:{uid}")        # Paladin's Guard: -25% incoming
        if red:
            dmg = max(1, round(dmg * (1.0 - red)))
        _drain_arcane_shield(area, uid, dmg)
    hp = max(0, _php.get(uid, PLAYER_MAXHP) - dmg)
    _php[uid] = hp
    player = f"p:{uid}"
    attack = {"Cmd": "Attack", "Caster": mon_ts, "Slot": -1, "StatusCode": 1,
              "Wait": False, "Error": "",
              # "Immediate": true is REQUIRED on monster->player damage: it makes the client dispense
              # the damage ticket NOW. Without it the ticket is queued, and NodeDispenseDamage only
              # runs for a main-player caster, so a monster's ticket never settles -> the player never
              # reaches `damageTickets==0` -> Entity.Die() (the 10s respawn screen) never fires.
              # Captured monster autos always carry this full three-node shape.
              # Damage alone changes HP but leaves the monster visually frozen.
              "Nodes": [{"Name": "Damage", "DamageTypes": [dtype], "Damages": [dmg],
                         "Targets": [player], "TargetHPs": [hp], "Immediate": True},
                        {"Name": "PlayerAnimation", "Animation": "Attack1,Attack2,Attack3",
                         "Priority": "Low", "Speed": 1.0, "Targets": 1},
                        {"Name": "Cooldown", "Animation": "", "Slot": 0,
                         "CD": int(MON_ATTACK_CD * 1000)}]}
    return attack, hp, hp <= 0


# --- monster TILE skills (telegraphed AoE; "however AE does it") --------------------------
# A monster whose class graph has a tile node periodically casts it: the server broadcasts a
# MonReq (ResponseMonReq) carrying the node; each client renders the telegraph (HitTiles with
# Shape="VerticalRectangle" is Ragnafluff's red bars) and, when it finishes, reports via gmah
# (RequestMonHit) whether ITS player was caught. The server applies the damage on that report
# — client-authoritative positional hit detection, exactly the AE model. We only pace the cast
# and remember the armed skill's multiplier so a later gmah resolves the right damage.
_mon_skill = {}            # (area, mon_ts) -> {"last": ts, "cd": secs, "mult": float, "idx": int}
_mon_tile_last = {}        # (area, mon_ts, uid) -> last tile-damage ts (rate cap, see below)
# Tile skills are client-reported (gmah) and a single cast can fire MANY reports: HitStream's
# HotTile reports on BOTH collider Enter AND Exit, the firewalls are 4 separate walls, and a
# lingering 15s zone re-toggles as you move. Applying a full swing per report stacks into a
# one-shot. So we cap tile damage to ONE application per player per this window (regardless of how
# many tiles/toggles land in it) — the boss stays threatening but can't gib you in a single frame.
_TILE_HIT_THROTTLE = 0.5


def monster_skill_due(area, mon_ts, now):
    """Whether a monster's next tile skill is off cooldown. The cooldown is the one stored by the
    LAST cast (per-skill cadence); the first cast is always due."""
    st = _mon_skill.get((area, mon_ts))
    speed = monster_speed_multiplier(area, mon_ts)
    if speed <= 0:
        return False
    return st is None or (now - st.get("last", 0.0)) >= st.get("cd", 5.0) / speed


def monster_skill_index(area, mon_ts):
    """The rotation index of the monster's last-cast tile skill (-1 if none yet)."""
    st = _mon_skill.get((area, mon_ts))
    return st.get("idx", -1) if st else -1


def arm_monster_skill(area, mon_ts, now, cd_s, mult, idx):
    """Record a just-fired tile cast: stores the cooldown-to-next, the damage multiplier a later
    gmah report uses, and the rotation index so the next cast advances to the following skill."""
    _mon_skill[(area, mon_ts)] = {"last": now, "cd": float(cd_s or 5.0),
                                  "mult": float(mult or 1.0), "idx": int(idx)}


def monster_tile_hit(area, mon_ts, uid, hits=1):
    """Apply `hits` reported tile-skill hits to a player (gmah). Per-hit damage = the monster's
    level swing * the armed skill multiplier (Weakened/Inhibition still apply). Returns
    (attack_packet, hp, died); (None, hp, False) if the monster has no armed skill (a stale
    report) or hits<=0 (the player escaped the red)."""
    st = _mon_skill.get((area, mon_ts))
    if st is None or hits <= 0:
        return None, _php.get(uid, PLAYER_MAXHP), False
    now = time.time()
    k = (area, mon_ts, uid)
    if now - _mon_tile_last.get(k, 0.0) < _TILE_HIT_THROTTLE:
        return None, _php.get(uid, PLAYER_MAXHP), False   # rate-capped: ignore the burst of reports
    _mon_tile_last[k] = now
    hits = min(hits, 2)                                    # cap a single window's stacked hits
    total = 0
    for _ in range(hits):
        d = _monster_dmg(area, mon_ts)
        out_mult = outgoing_damage_multiplier(area, mon_ts, physical=True)
        if out_mult != 1.0:
            d = max(1, round(d * out_mult))
        red = _guard_reduction(area, f"p:{uid}")        # Paladin's Guard: -25% incoming
        if red:
            d = max(1, round(d * (1.0 - red)))
        landed = max(1, round(d * st["mult"]))
        total += landed
        _drain_arcane_shield(area, uid, landed)
    hp = max(0, _php.get(uid, PLAYER_MAXHP) - total)
    _php[uid] = hp
    player = f"p:{uid}"
    # Same Damage-node shape as monster_attack: Immediate so the client settles the ticket and
    # the death flow can fire (see the monster_attack note on Immediate).
    attack = {"Cmd": "Attack", "Caster": mon_ts, "Slot": -1, "StatusCode": 1,
              "Wait": False, "Error": "",
              "Nodes": [{"Name": "Damage", "DamageTypes": [DT_NORMAL], "Damages": [total],
                         "Targets": [player], "TargetHPs": [hp], "Immediate": True}]}
    return attack, hp, hp <= 0


# --- summoned adds (Ragnafluff's clones) --------------------------------------------------
# A boss skill can SUMMON other monsters (server-side spawnMob). Spawned adds aren't in the map's
# pad layer, so they get a NEGATIVE MonMapID (m:-N) — never collides with a real pad id, and lets
# us tell summons apart (e.g. to skip respawn on death). Each is registered in combat like any
# monster and aggro'd onto the boss's target, then driven by the normal AI swing loop.
_summoned = {}             # (area, boss_ts) -> set(clone_ts)
_next_summon_id = {}       # area -> next negative MonMapID to hand out


def is_summoned_ts(target_string):
    """True if a target string is a summoned add (negative MonMapID)."""
    try:
        return target_string.startswith("m:") and int(target_string[2:]) < 0
    except (ValueError, AttributeError):
        return False


def live_summon_count(area, boss_ts):
    """How many of a boss's summoned adds are currently alive."""
    s = _summoned.get((area, boss_ts))
    return sum(1 for ts in s if _mon.get((area, ts), 0) > 0) if s else 0


def add_summon(area, boss_ts, mon_id, hp, level, frame=None, race=None, element=None):
    """Register a freshly spawned add in combat (HP/identity) under its boss. Returns the add's
    target string m:<negativeMapID> (also the MonMapID to put in the spawnMob monBranch)."""
    nid = _next_summon_id.get(area, -1)
    _next_summon_id[area] = nid - 1
    ts = f"m:{nid}"
    register_monster(area, ts, hp, mon_id=mon_id, frame=frame, level=level,
                     race=race, element=element)
    _summoned.setdefault((area, boss_ts), set()).add(ts)
    return ts


def forget_summon(area, clone_ts):
    """Drop a dead add from combat state (no respawn for summons)."""
    drop_auras_for(clone_ts)
    _mon.pop((area, clone_ts), None)
    _maxhp.pop((area, clone_ts), None)
    _moninfo.pop((area, clone_ts), None)
    _mirror_break.pop((area, clone_ts), None)   # player killed it first -> cancel the self-break/stun
    for key, s in list(_summoned.items()):
        if key[0] == area:
            s.discard(clone_ts)


# --- Groglurk's Mirror: a summoned add the BOSS shatters on a timer, stunning its target -----
# A normal add is killed by the player; a mirror with a self-break timer is instead broken by the
# boss after `break_secs`, and THAT break stuns the target for `stun_secs`. If the player destroys
# the mirror first, forget_summon cancels the timer and no stun happens (the intended mechanic).
# The stun is client-enforced: statusEffect Stun(1) sets Entity.isStunned (blocks combat/move/
# interact); status 0 clears it (StatusEffect.Execute in the decompiled client).
_mirror_break = {}          # (area, clone_ts) -> {"at": ts, "uid": uid, "stun_secs": float}


def arm_mirror_break(area, clone_ts, uid, break_secs, stun_secs):
    """Schedule the boss to shatter this mirror `break_secs` from now, stunning uid for stun_secs."""
    _mirror_break[(area, clone_ts)] = {"at": time.time() + float(break_secs),
                                       "uid": uid, "stun_secs": float(stun_secs)}


def due_mirror_breaks(now):
    """Mirrors whose self-break timer elapsed. Pops every elapsed entry; returns
    [(area, clone_ts, uid, stun_secs)] for the ones STILL ALIVE (player didn't break them first) —
    a player-killed mirror is dropped silently (no stun, per the mechanic)."""
    out = []
    for key, info in list(_mirror_break.items()):
        if now >= info["at"]:
            _mirror_break.pop(key, None)
            if _mon.get(key, 0) > 0:
                out.append((key[0], key[1], info["uid"], info["stun_secs"]))
    return out


def stun_packets(uid, name="Stunned", caster=""):
    """s2c packets that STUN a player: statusEffect Stun(1) (-> Entity.isStunned) + a 'Stunned'
    aura Add for the on-screen popup (auraCmd 0 = Add)."""
    return [
        {"Cmd": "statusEffect", "targetString": f"p:{uid}", "status": 1},
        {"Cmd": "AuraChange", "auraCmd": 0, "nam": name, "Target": f"p:{uid}",
         "casterTS": caster, "Icon": "", "uniquenessType": 0},
    ]


def unstun_packets(uid, name="Stunned"):
    """s2c packets that CLEAR the stun: statusEffect Normal(0) + the 'Stunned' aura Remove
    (auraCmd 1 -> the '<name> Fades' popup)."""
    return [
        {"Cmd": "statusEffect", "targetString": f"p:{uid}", "status": 0},
        {"Cmd": "AuraChange", "auraCmd": 1, "nam": name, "Target": f"p:{uid}",
         "casterTS": "", "Icon": ""},
    ]


def drop_aggro_for(uid):
    """Stop every monster currently chasing this player (e.g. on death/respawn)."""
    for key, info in list(_aggro.items()):
        if info.get("uid") == uid:
            _aggro.pop(key, None)
            _mon_skill.pop(key, None)


# --- continuous auto-attack (server-driven, like the monster AI) -------------
# Double-clicking a monster should keep auto-attacking it. The client has its own
# auto loop but it stalls through our handshake, so the SERVER sustains it: when a
# player auto-attacks, we remember the (graph, target) and the AI loop re-fires it
# on the auto cooldown until the target dies or the player leaves. Both the client's
# gar[0] and the loop share off_cooldown(uid, 0, ...), so they can't double-fire.
_auto = {}                  # uid -> {"area","target","data","forge","cd","skill_id"}


def auto_engage(uid, area, target, data, forge, cd_ms, skill_id=None):
    """Remember an auto with every input needed to repeat the same execution path."""
    if valid_combat_target(area, target):
        _auto[uid] = {"area": area, "target": target, "data": data,
                      "forge": forge, "cd": cd_ms, "skill_id": skill_id}
        engage(area, target, uid)


def auto_disengage(uid):
    _auto.pop(uid, None)


def auto_engaged(uid, area=None, target=None):
    """Whether this player already has a sustained auto on the requested combat target."""
    active = _auto.get(uid)
    if active is None:
        return False
    if area is not None and active.get("area") != area:
        return False
    if target is not None and active.get("target") != target:
        return False
    return True


def cancel_player_actions(uid, clear_cooldowns=False):
    """Cancel every in-flight/repeating action owned by one player.

    Used on death, cell/map changes, and class swaps so an old graph, delayed
    field, or sustained auto cannot execute in a new combat context.
    """
    auto_disengage(uid)
    drop_aggro_for(uid)
    for ctx, cast in list(_casts.items()):
        if cast.uid == uid:
            _casts.pop(ctx, None)
    caster = f"p:{uid}"
    _delayed[:] = [row for row in _delayed
                   if not (isinstance(row[3], tuple) and row[3]
                           and row[3][0] == caster)]
    _conv_cast_stacks.pop(uid, None)
    if clear_cooldowns:
        for key in [key for key in _cast_last if key[0] == uid]:
            _cast_last.pop(key, None)


def auto_engagements():
    return [(uid, a["area"], a["target"], a["data"], a["forge"], a["cd"],
             a.get("skill_id"))
            for uid, a in list(_auto.items())]


def execute_auto(area, uid, target, data, forge, skill_id=None, allies=None):
    """Execute one manual or sustained auto through the same class-aware route.

    Data-driven classes must retain their rule config and skill id on repeat;
    legacy authored autos deliberately use the single-shot graph walker so an
    autonomous server tick never opens an igai/gai client handshake.
    """
    if not valid_combat_target(area, target):
        return [], [], 0
    if data is not None and class_rules(uid, skill_id) is not None:
        packets, killed, damage = begin_cast(
            area, uid, 0, target, data, forge, skill_id, allies)
    elif data is not None:
        attack, killed, damage = cast_skill(
            area, uid, 0, target, data, forge, skill_id, allies)
        packets = [attack]
    else:
        attack, hit, damage = auto_attack(area, target, uid)
        killed = [target] if hit else []
        packets = [attack]
    engage(area, target, uid)  # refresh the aggro lease while autos are landing
    return packets, killed, damage


def rest_player(uid, name):
    """Out-of-combat rest: full-heal HP and, for mana classes, refill the resource pool.
    -> hpmp packet (ResponseHpMp: sets the entity's HP/RP/State). Determination classes have
    no MP to refill (it's built in combat), so only their HP is restored."""
    mx = _pmax.get(uid, PLAYER_MAXHP)
    _php[uid] = mx
    if _resource_model.get(uid) == "mana":
        _rp[uid] = _rp_max.get(uid, MAX_RP)
    # unm MUST be lowercase: the client resolves it via Entity.getPlayer(unm), and players are
    # registered under their lowercase name (the chat-bubble fix). Mixed case -> no match -> no-op.
    return {"Cmd": "hpmp", "unm": (name or "").lower(), "HP": mx, "RP": _rp.get(uid, 0), "State": 1}


def revive_player(uid, name):
    """Full-heal a downed player -> playerRes packet (ResponseResPlayer keys on the
    player NAME). Also drops aggro so they aren't re-killed at the respawn point."""
    mx = _pmax.get(uid, PLAYER_MAXHP)
    _php[uid] = mx
    cancel_player_actions(uid)
    return {"Cmd": "playerRes", "unm": (name or "").lower(), "HP": mx, "MaxHP": mx}  # lowercase: see rest_player


def lethal_self_packet(uid):
    """An Attack node that drops the player to 0 HP so their OWN client runs the real death
    flow (DamageText -> Entity.Die -> showRespawnUI -> 10s timer -> resPlayerTimed). Used by /die.
    Broadcast to the area so everyone sees the death too."""
    cur = _php.get(uid, PLAYER_MAXHP)
    _php[uid] = 0
    player = f"p:{uid}"
    return {"Cmd": "Attack", "Caster": player, "Slot": -1, "StatusCode": 1,
            "Wait": False, "Error": "",
            "Nodes": [{"Name": "Damage", "DamageTypes": [DT_NORMAL], "Damages": [max(1, cur)],
                       "Targets": [player], "TargetHPs": [0], "Immediate": True}]}


def player_death_packet(uid, killer_ts):
    """entityDeath for a slain PLAYER. CONFIRMED from the capture: live AE downs a player with an
    `entityDeath` targeting "p:<uid>" (NOT PlayerDeath) right after the lethal hit. The client runs
    Entity.Die -> the 10s respawn screen for the victim (death anim for everyone else), then auto-
    sends resPlayerTimed. PlayerDeath was wrong: it pre-sets state=Dead and Die() early-returns."""
    return {"Cmd": "entityDeath", "targetString": f"p:{uid}",
            "efbTargetString": killer_ts or f"p:{uid}"}


def death_packets(area, target, uid):
    """entityDeath + mKill for a slain monster. The monster stays dead (HP 0) until
    respawn_packet re-spawns it; aggro on it is cleared."""
    _last.pop(uid, None)
    disengage(area, target)
    drop_auras_for(target)                  # per-life effects (including Freeze Immune) reset
    caster = f"p:{uid}"
    return [
        {"Cmd": "entityDeath", "targetString": target, "efbTargetString": caster},
        {"Cmd": "mKill", "targetString": target},
    ]


def respawn_packet(area, target):
    """Reset a dead monster to full HP and return the RespawnMon broadcast that makes
    it visually reappear (ResponseRespawnMon.Execute -> Monster.Respawn())."""
    key = (area, target)
    drop_auras_for(target)
    _mon[key] = _maxhp.get(key, DEFAULT_HP)
    try:
        mon_map_id = int(target.split(":", 1)[1])
    except (ValueError, IndexError):
        mon_map_id = 0
    info = _moninfo.get(key, {})
    return {"Cmd": "RespawnMon", "monID": int(info.get("mon_id") or 0),
            "monMapID": mon_map_id, "Frame": info.get("frame") or "Enter"}


def reward_packet(exp_total, gold_total):
    return {"Cmd": "addGoldXP",
            "Exp": {"val": XP_PER_KILL}, "ExpTotal": exp_total,
            "Rep": {}, "factionID": 1,
            "Gold": {"val": GOLD_PER_KILL}}
