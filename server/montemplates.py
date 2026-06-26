"""
Monster/NPC catalog: MonID -> a full Monbranch (with the art Bundle).

The in-game NPC editor speaks in PadData/NPCEditData, which carry stats but NOT
the asset-bundle reference an avatar needs to render. Every captured monBranch
entry DOES carry that Bundle (npcs/<id>_<name>.unity3d) plus strLinkage,
equippedItems, behaviour, etc. So we keep one canonical Monbranch per MonID.

The authoritative store is now the `monsters` DB table (so monsters are editable
and visible). `seed_db` fills it from the captured maps; `get`/`template` read it
back. A captured set is still parsed from data/maps/*.json as the seed source.
"""
import json
import pathlib
import urllib.request

import monrecord

_MONCOLS = ",".join(f'"{c}"' for c in monrecord.ALL_COLS)   # SELECT list for the canonical record


def store(conn, mon_id, raw, cat=None, replace=False):
    """Decompose a monBranch (+ optional crawled catalog def) into the canonical columns and
    upsert. The two wire shapes are regenerated from these columns on read (get/catalog)."""
    rec = monrecord.from_dicts(raw or {}, cat or {})
    rec["mon_id"] = int(mon_id)
    row = monrecord.cols_to_row(rec)
    qc = ",".join(f'"{c}"' for c in monrecord.ALL_COLS)
    ph = ",".join("?" for _ in monrecord.ALL_COLS)
    if replace:
        action = "DO UPDATE SET " + ", ".join(
            f'"{c}"=excluded."{c}"' for c in monrecord.ALL_COLS if c != "mon_id")
    else:
        action = "DO NOTHING"
    conn.execute(f"INSERT INTO monsters ({qc}) VALUES ({ph}) ON CONFLICT(mon_id) {action}",
                 tuple(row[c] for c in monrecord.ALL_COLS))


MAPS_DIR = pathlib.Path(__file__).resolve().parent.parent / "data" / "maps"
MONSTERS_DIR = pathlib.Path(__file__).resolve().parent.parent / "data" / "monsters"
UPSTREAM_MON = "https://infinity.aq.com/game/api/data/GetMonsterData?ids="

# Placement fields stripped from a template (the pad supplies them).
_PLACEMENT_FIELDS = ("x", "y", "fx", "fy", "MonMapID", "strFrame", "direction",
                     "intState", "NPCRequirementData", "apopID")


def _completeness(mb):
    """Rank captured entries so the richest (one with art Bundle) wins on dedupe."""
    score = 0
    if mb.get("Bundle"):
        score += 1000
    if mb.get("strLinkage"):
        score += 100
    if mb.get("equippedItems"):
        score += 10
    return score + len(mb)


def file_catalog():
    """MonID -> richest captured Monbranch, parsed from data/maps/*.json.
    Used only to seed the DB."""
    catalog = {}
    if not MAPS_DIR.exists():
        return catalog
    for f in MAPS_DIR.glob("*.json"):
        try:
            area = (json.loads(f.read_text(encoding="utf-8")) or {}).get("area") or {}
        except Exception:
            continue
        for mb in area.get("monBranch") or []:
            mid = mb.get("MonID")
            if mid is None:
                continue
            if mid not in catalog or _completeness(mb) > _completeness(catalog[mid]):
                catalog[mid] = mb
    return catalog


def _crawled_catalog():
    """MonID -> AE GetMonsterData crawled def, parsed from data/monsters/<id>.json.
    The full AE catalog (~410), DB-resident now that R2 is gone."""
    out = {}
    if not MONSTERS_DIR.exists():
        return out
    for f in MONSTERS_DIR.glob("*.json"):
        if f.name == "index.json":
            continue
        try:
            c = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        if isinstance(c, dict) and c.get("ID") is not None:
            out[int(c["ID"])] = c
    return out


def seed_db(conn):
    """Seed the full monster catalog into the monsters table, INSERT-IF-ABSENT (existing rows are
    left untouched so in-game edits survive). Each new MonID gets a row carrying BOTH its monBranch
    (`raw`, the captured-rich one where we have it, otherwise derived from the crawled def) and the
    crawled GetMonsterData def (`catalog`). Union of the captured set (data/maps/*.json) and the
    crawled set (data/monsters/*.json)."""
    captured = file_catalog()                       # MonID -> rich captured monBranch
    crawled = _crawled_catalog()                    # MonID -> GetMonsterData def
    n = 0
    for mid in set(captured) | set(crawled):
        cat = crawled.get(mid)
        mb = captured.get(mid) or _catalog_to_monbranch(cat)
        store(conn, mid, mb, cat)
        n += 1
    return n


def _catalog_to_monbranch(c):
    """Map an AE GetMonsterData CATALOG def (ID/Name/Level/Class/Bundle) to our monBranch shape,
    so a monster we crawled but never captured in a map is still spawnable. The catalog carries no
    HP (that's per-placement in AE) — default it by level (flagged: OUR heuristic)."""
    lvl = int(c.get("Level") or 1)
    hp = max(100, lvl * 150)
    return {
        "MonID": int(c["ID"]), "ID": int(c["ID"]), "strMonName": c.get("Name"),
        "strSubtitle": c.get("strSubtitle") or "", "strLinkage": c.get("strLinkage"),
        "Level": c.get("Level"), "sRace": c.get("sRace"), "strElement": c.get("strElement"),
        "Class": c.get("Class"), "Bundle": c.get("Bundle"), "Gender": c.get("Gender") or "M",
        "intHP": hp, "intHPMax": hp, "equippedItems": c.get("equippedItems") or {},
    }


def resolve_upstream(conn, ids):
    """Pull unknown monster catalog defs from AE's public GetMonsterData and cache them (catalog
    + a derived monBranch), so newly-encountered monsters render/fight without a manual crawl.
    HP is the level heuristic until that monster's real placement is captured. Best-effort."""
    missing = [i for i in ids
               if conn.execute("SELECT 1 FROM monsters WHERE mon_id=?", (i,)).fetchone() is None]
    if not missing:
        return 0
    try:
        req = urllib.request.Request(UPSTREAM_MON + ",".join(str(i) for i in missing),
                                     headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=20) as r:
            arr = json.loads(r.read().decode("utf-8"))
    except Exception as ex:
        print(f"  [api] monster proxy FAIL ({len(missing)} ids): {ex}")
        return 0
    n = 0
    for cat in arr if isinstance(arr, list) else []:
        try:
            mid = int(cat["ID"])
        except (KeyError, TypeError, ValueError):
            continue
        mb = _catalog_to_monbranch(cat)
        store(conn, mid, mb, cat)
        n += 1
    if n:
        conn.commit()
        print(f"  [api] learned {n} monsters from AE (proxy)")
    return n


def catalog(conn, mon_id):
    """The GetMonsterData CATALOG def for a MonID — GENERATED from the canonical record
    (monrecord.to_catalog) so it can't drift from the spawn monBranch the way the old stored
    `catalog` column did (that drift served stale element/apopID/Bundle and broke NPC apop
    portraits). `raw` is authoritative; the stored catalog contributes only its avatar-
    customization fields (colours/hair). None if we have no row at all."""
    row = conn.execute(f"SELECT {_MONCOLS} FROM monsters WHERE mon_id=?",
                       (int(mon_id),)).fetchone()
    if row is None:
        return None
    return monrecord.to_catalog(monrecord.row_to_cols(row))


def get(conn, mon_id):
    """The canonical Monbranch for a MonID (deep copy), or None. The monsters table is the
    authoritative store — captured-rich where we have it, derived from the crawled catalog
    otherwise (both seeded at startup), and editable in place."""
    row = conn.execute(f"SELECT {_MONCOLS} FROM monsters WHERE mon_id=?", (int(mon_id),)).fetchone()
    return monrecord.to_monbranch(monrecord.row_to_cols(row)) if row else None


# Curated canonical columns the monster editor exposes (the rest ride untouched in the columns).
EDITOR_COLS = ["name", "subtitle", "linkage", "hp", "hp_max", "mp_max", "race", "element",
               "level", "gender", "class_id", "behave", "scale", "apop_id", "no_move", "b_red"]


def editor_load(conn, mon_id):
    """{monster:{col:val,...}, drops:[{item_id,rate,quantity}]} for the monster editor, or {}."""
    row = conn.execute(f"SELECT {_MONCOLS} FROM monsters WHERE mon_id=?", (int(mon_id),)).fetchone()
    if row is None:
        return {}
    cols = monrecord.row_to_cols(row)
    mon = {c: cols.get(c) for c in EDITOR_COLS}
    mon["mon_id"] = int(mon_id)
    drops = [{"item_id": r["item_id"], "rate": r["rate"], "quantity": r["quantity"]}
             for r in conn.execute("SELECT item_id, rate, quantity FROM monster_drops "
                                   "WHERE mon_id=? ORDER BY item_id", (int(mon_id),))]
    return {"monster": mon, "drops": drops}


def editor_save(conn, payload):
    """Persist monster column edits + replace its drop table. {ok,ID} or {ok:False,msg}."""
    mon = payload.get("monster") or {}
    try:
        mid = int(mon.get("mon_id"))
    except (TypeError, ValueError):
        return {"ok": False, "msg": "Monster needs a numeric mon_id."}
    if conn.execute("SELECT 1 FROM monsters WHERE mon_id=?", (mid,)).fetchone() is None:
        return {"ok": False, "msg": f"monster {mid} not found."}
    sets, vals = [], []
    for c in EDITOR_COLS:
        if c in mon:
            sets.append(f'"{c}"=?')
            vals.append(mon[c])
    if sets:
        conn.execute(f"UPDATE monsters SET {', '.join(sets)} WHERE mon_id=?", tuple(vals) + (mid,))
    conn.execute("DELETE FROM monster_drops WHERE mon_id=?", (mid,))
    for d in (payload.get("drops") or []):
        try:
            iid = int(d.get("item_id"))
        except (TypeError, ValueError):
            continue
        if conn.execute("SELECT 1 FROM items WHERE item_id=?", (iid,)).fetchone() is None:
            return {"ok": False, "msg": f"item {iid} isn't in the catalog (add it first)."}
        conn.execute("INSERT INTO monster_drops(mon_id, item_id, rate, quantity) VALUES(?,?,?,?) "
                     "ON CONFLICT(mon_id, item_id) DO UPDATE SET rate=excluded.rate, "
                     "quantity=excluded.quantity",
                     (mid, iid, float(d.get("rate", 0.1) or 0.1), int(d.get("quantity", 1) or 1)))
    conn.commit()
    return {"ok": True, "ID": mid}


def template(conn, mon_id):
    """An identity-only Monbranch for a MonID: catalog entry with placement
    fields stripped, or a minimal stub if we never captured this monster."""
    mb = get(conn, mon_id)
    if mb is None:
        return {
            "MonID": int(mon_id), "ID": int(mon_id),
            "strMonName": f"Mon {mon_id}", "intHP": 100, "intHPMax": 100,
            "Level": 1, "equippedItems": {},
        }
    for k in _PLACEMENT_FIELDS:
        mb.pop(k, None)
    return mb
