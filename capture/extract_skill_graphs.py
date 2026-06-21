"""Reconstruct authored skill node-graphs for ALL classes from the live capture, and
write data/skill_graphs.json {skill_id: {"name","data","forge"}}.

One cast's resolved nodes = the concatenation of its Attack batches (which already include
the resolved input nodes). We take the FIRST clean completed cast per skill, then convert
each resolved node back to its AUTHORED form (keep the animation/particle/sound/aura/cooldown
/range structure; drop server-computed Damages/TargetHPs/validated targets) and emit a linear
graph with Self/Target helper nodes wired into Damage/Aura.
"""
import json, re, pathlib
CAP = r"C:/Program Files (x86)/Steam/steamapps/common/AdventureQuest Worlds Unity Playtest/UserData/Beyond/packets.jsonl"

def cls_of(icon): return re.split(r"[\/]", icon or "")[0]

done = {}            # skill_id -> (name, [resolved nodes]) : first clean cast
slotmap = {}; names = {}
active = None

def finish():
    global active
    if active and active["nodes"] and active["done"] and active["sid"] not in done:
        done[active["sid"]] = (active["name"], active["nodes"])
    active = None

with open(CAP, encoding="utf-8", errors="replace") as fh:
    for line in fh:
        if not any(k in line for k in ('"sEAct"','"sAct"','"gar"','"Attack"')): continue
        try: o = json.loads(line)
        except Exception: continue
        p = o.get("pkt", {}); c = p.get("Cmd")
        if c in ("sEAct","sAct"):
            sl = p.get("skillList") or {}
            slotmap = {int(k): v.get("id") for k,v in sl.items()}
            names   = {int(k): v.get("nam") for k,v in sl.items()}
        elif c == "gar":
            finish()
            try: slot = int(p.get("Params",["x"])[0])
            except Exception: slot = None
            if slot in slotmap:
                active = {"sid": slotmap[slot], "slot": slot, "name": names.get(slot),
                          "nodes": [], "done": False}
        elif c == "Attack" and active and not active["done"]:
            slot = p.get("Slot")
            if slot == active["slot"] or (active["slot"] == 0 and slot in (None,-1,0)):
                active["nodes"].extend(p.get("Nodes", []))
                if p.get("StatusCode") == 1:
                    active["done"] = True
finish()

# ---- resolved node -> authored node -------------------------------------------------
KEEP = {
 "Range": ["HRange","VRange"], "RangeMulti": ["HRange","VRange"],
 "Cooldown": ["CD","Animation"], "Resource": ["Amount"],
 "SoundFX": ["Sound","Animation","Time"], "ImpactSoundFX": ["Sound","Animation","Time"],
 "Particle": ["Particle","Animation","X","Y","Time","Follow"],
 "PlayerAnimation": ["Animation","Priority","Speed"], "SpellAnimation": ["Animation","Speed"],
 "AnimationHitbox": ["X","Y","Width","Height","Animation","Speed","Time"],
 "Hitbox": ["X","Y","Width","Height"],
 "Restrict": ["Movement","Skills","Slot","Animation","Time"],
 "Interruptable": ["Animation","Time"], "MoveTargets": ["Mode","Distance"],
 "AuraVFX": ["AuraName","VFX"], "UpdateAnimation": ["Tag","Value"],
 "DashToTarget": ["Animation"], "DispenseDamage": [], "ImpactAura": ["AuraName"],
 "SpawnPickup": [], "Message": ["Message"],
}
def authored(n):
    name = n.get("Name")
    if name == "Damage":
        return {"Name":"Damage","DamageType":"Physical","Multiplier":1.0,"__target":True}
    if name == "Aura":
        tgts = n.get("Targets") or []
        self_buff = bool(tgts and all(isinstance(t,str) and t.startswith("p:") for t in tgts))
        return {"Name":"Aura","AuraName":n.get("AuraName") or "","Animation":n.get("Animation") or "",
                "__helper": "Self" if self_buff else "Target"}
    if name in KEEP:
        out = {"Name": name}
        for k in KEEP[name]:
            if n.get(k) is not None: out[k] = n[k]
        return out
    return None

def build_graph(resolved):
    """Linear graph (data, forge) from a resolved node list, adding Self/Target helpers."""
    nodes = {}; order = []; helpers = {}; nid = 0
    def helper(kind):
        if kind not in helpers:
            nonlocal nid; nid += 1; hid = f"h{nid}"
            helpers[kind] = hid; nodes[hid] = {"Name": kind}
        return helpers[kind]
    prev = None  # dedup identical consecutive nodes (collapse re-sent batches)
    for rn in resolved:
        a = authored(rn)
        if a is None: continue
        sig = json.dumps(a, sort_keys=True)
        if sig == prev:           # skip an immediate duplicate (re-sent leading node)
            continue
        prev = sig
        nid += 1; this = f"n{nid}"
        if a.pop("__target", False):
            a["Targets"] = {"id": helper("Target")}
        hk = a.pop("__helper", None)
        if hk:
            a["Targets"] = {"id": helper(hk)}
        nodes[this] = a; order.append(this)
    # header + linear Next chain
    data_nodes = dict(nodes)
    def chain(i):
        if i >= len(order): return None
        d = {"id": order[i]}
        nxt = chain(i+1)
        if nxt: d["Next"] = nxt
        return d
    tree = {"0": ({"Next": chain(0)} if order else {})}
    data = [{"0": {"Name":"OnRequest"}}, data_nodes]
    pos = {"0": {"X":-1000.0,"Y":0.0}}
    for i,k in enumerate(order): pos[k] = {"X": float(i*240-700), "Y": 0.0}
    for hk,hid in helpers.items(): pos[hid] = {"X": 0.0, "Y": 200.0}
    return data, [pos, tree]

out = {}
for sid,(nm,resolved) in done.items():
    data, forge = build_graph(resolved)
    out[str(sid)] = {"name": nm, "data": data, "forge": forge}
pathlib.Path("data/skill_graphs.json").write_text(json.dumps(out, separators=(",",":")))
print("wrote data/skill_graphs.json for", len(out), "skills:", sorted(int(s) for s in out))
for sid in (115,141,136):
    if str(sid) in out:
        names_seq=[p.get("Name") for p in out[str(sid)]["data"][1].values()]
        print(f"  {sid} {out[str(sid)]['name']}: {names_seq}")
