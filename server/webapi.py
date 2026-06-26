#!/usr/bin/env python3
"""
Local web API for AQW Infinity — the HTTP side of the private server.

The Unity client makes plain HTTP+JSON calls to `Main.WebApiURL` (normally
https://infinity.aq.com/game/api/) for things the TCP game server doesn't carry:
monster data, base classes, soundtracks, server list, and the dev authoring tools
(tweak/CreateNewApop, tweak/DialoggerSave/Load). With the mod's ApiPatch pointing
WebApiURL at us, EVERY one of those lands here and is answered from our own DB —
no runtime dependency on AE.

Endpoints the client calls (from decomp; grep "WebApiURL + "):
  GET  data/GetMonsterData?ids=a,b      -> List<Monbranch>      (monsters table)
  GET  data/getsoundtracks?ids=a,b      -> soundtrack list
  GET  data/GetAssetBundlesByIDs?ids=   -> List<AssetBundleData>
  GET  data/Servers?flavor=2            -> server list
  GET  Data/GetBaseClasses              -> base class defs
  GET  Data/InfinityVars                -> client config vars
  POST tweak/CreateNewApop {staff,s_key,npcID} -> {"ID": n}     (apops table)
  POST tweak/DialoggerSave / DialoggerLoad     -> dialog editor I/O
  + Login/*, steam/, Mobile/ValidateReceipt    (auth — bypassed via TCP login)

CAPTURE_PROXY: a one-time learning aid. When True, an endpoint we don't serve yet
is forwarded to UPSTREAM and the request+response logged to webapi_capture.jsonl
so we can copy the exact shape, then implement it here. Default False (fully local;
unimplemented endpoints return an empty stub and are logged to webapi_unhandled).
"""
import http.server
import socketserver
import base64
import hashlib
import hmac
import json
import os
import pathlib
import time
import urllib.parse
import urllib.request

import db
import game
import montemplates

HOST = "0.0.0.0"
PORT = 8182                                   # mod ApiPatch rewrites WebApiURL -> here

# Where the account bundle tells the client to find the GAME server (TCP). Defaults to localhost
# for local dev; the hosted deploy sets INFINITY_PUBLIC_HOST=<public IP> so clients connect to the VM.
PUBLIC_HOST = os.environ.get("INFINITY_PUBLIC_HOST", "127.0.0.1")
GAME_PORT = int(os.environ.get("INFINITY_GAME_PORT", "5588"))
CAPTURE_PROXY = False                         # one-time shape learning; then keep False
UPSTREAM = "https://infinity.aq.com/game/api/"

# Staff gate for the editor tools (the pages + all apop/* and quest/* endpoints). Authoring tools
# that write game content, reachable both via Caddy and directly on :8182 — so the gate lives in
# the app. Staff log in with their REAL game account; access is gated by access_level (a normal
# player authenticates but is rejected). The session is a signed cookie (HMAC over EDIT_SECRET);
# EDIT_SECRET is just the server-side signing key, NOT a shared password. FAIL CLOSED: no secret
# configured -> the editors refuse to serve. Reuses INFINITY_EDIT_PASS as the signing key so no new
# env var is required (its role changed from a password to a signing secret).
EDIT_SECRET = (os.environ.get("INFINITY_EDIT_SECRET")
               or os.environ.get("INFINITY_EDIT_PASS") or "").encode("utf-8")
EDIT_MIN_ACCESS = int(os.environ.get("INFINITY_EDIT_MIN_ACCESS", "40"))   # staff tier (dev=100)
EDIT_SESSION_SECS = 12 * 3600
EDIT_COOKIE = "infinity_edit"


def _sign_session(username, access):
    """A signed, expiring session token: base64(payload).hmac. Stateless — no session table."""
    payload = {"u": username, "a": int(access), "exp": int(time.time()) + EDIT_SESSION_SECS}
    body = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode().rstrip("=")
    sig = hmac.new(EDIT_SECRET, body.encode(), hashlib.sha256).hexdigest()[:32]
    return f"{body}.{sig}"


def _verify_session(token):
    """The payload dict if the token is validly signed, unexpired, and meets EDIT_MIN_ACCESS; else None."""
    if not token or not EDIT_SECRET or "." not in token:
        return None
    body, sig = token.rsplit(".", 1)
    expect = hmac.new(EDIT_SECRET, body.encode(), hashlib.sha256).hexdigest()[:32]
    if not hmac.compare_digest(sig, expect):
        return None
    try:
        payload = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
    except Exception:
        return None
    if int(payload.get("exp", 0)) < time.time() or int(payload.get("a", 0)) < EDIT_MIN_ACCESS:
        return None
    return payload

LOG_DIR = pathlib.Path(__file__).resolve().parent
CAPTURE_LOG = LOG_DIR / "webapi_capture.jsonl"
UNHANDLED_LOG = LOG_DIR / "webapi_unhandled.jsonl"

# The "Characters" asset bundle the CharSelect screen preloads. Static client/asset config,
# identical for every player (not account data) — so it's a constant, not loaded from a capture.
CHARACTERS_BUNDLE = {
    "ID": 70955, "Name": "Characters", "Filename": "gameassets/70955_characters.unity3d",
    "VersionContent": 0, "VersionStage": 15, "VersionLive": 15, "Dirty": False,
    "version": 15, "VersionedFileName": "gameassets/70955_characters/15/70955_characters.unity3d",
}


# ---- endpoint implementations (served from OUR db) -------------------------

def _ids(qs):
    """Parse ?ids=1,2,3 -> [1,2,3]."""
    raw = urllib.parse.parse_qs(qs).get("ids", [""])[0]
    return [int(t) for t in raw.split(",") if t.strip().lstrip("-").isdigit()]


def get_monster_data(conn, qs):
    """data/GetMonsterData -> the AE monster catalog defs by id, served from the DB
    (montemplates.catalog reads the monsters.catalog column), 1=1 with AE's endpoint shape."""
    ids = _ids(qs)
    montemplates.resolve_upstream(conn, ids)        # pull any unknown monsters from AE, cache them
    out = []
    for mid in ids:
        c = montemplates.catalog(conn, mid)
        if c is not None:
            out.append(c)
    return out


# --- asset-bundle registry (id -> {ID,Name,Filename,Version*}) -----------------
# AssetBundleDataLoader (client) GETs data/GetAssetBundlesByIDs?ids=... to turn the bundle IDs
# embedded in cutscenes (Load{66131,...}), and other asset refs, into CDN filenames. It then does
# IDs.Select(id => cache[id]) — so if we omit ANY requested id it throws and the load HANGS
# (this is exactly what stalled the cutscene). We don't author AE's art, so we resolve unknown ids
# by proxying AE's API ONCE and accumulating them into data/asset_bundles.json — building our own
# registry over time (the .unity3d bundles themselves are AE's public CDN content, same as the map
# and item art we already load). Every requested id is always returned (a stub if still unresolved)
# so the client never hangs.
ASSET_REGISTRY = LOG_DIR.parent / "data" / "asset_bundles.json"
_bundles = {}                                   # id(int) -> AssetBundleData dict


def _load_bundle_registry():
    try:
        data = json.loads(ASSET_REGISTRY.read_text(encoding="utf-8"))
        for k, v in data.items():
            _bundles[int(k)] = v
    except (OSError, ValueError):
        pass


def _save_bundle_registry():
    try:
        ASSET_REGISTRY.parent.mkdir(parents=True, exist_ok=True)
        ASSET_REGISTRY.write_text(
            json.dumps({str(k): v for k, v in sorted(_bundles.items())}, separators=(",", ":")),
            encoding="utf-8")
    except OSError:
        pass


PROBE_CDN = "https://infinity.aq.com/game/assetbundles/windows/"   # the LIVE CDN the playtest client fetches from


def _probe_cdn_version(b):
    """The version of this bundle that ACTUALLY exists on the LIVE CDN the client fetches from
    (Player.log shows it requests infinity.aq.com, i.e. a LIVE build using VersionLive). AE's
    version metadata doesn't reliably match the CDN, and the client builds a /{version}/ URL
    that 404s if it's wrong — which hangs asset loading (e.g. cutscene 66394 is at v4 on live,
    66362 at v0, 66365 at v2). Probe the distinct candidate versions on the live CDN and use the
    first that resolves. Cached on the bundle (_cdnvL). Note: an earlier version probed contentinf
    and served v0, which 404'd on the live CDN — the CDN must match what the client uses."""
    fn = b.get("FileName") or b.get("Filename") or ""
    if not fn.endswith(".unity3d"):
        return None
    noext = fn[:-len(".unity3d")]
    base = noext.rsplit("/", 1)[-1] + ".unity3d"
    seen, cands = set(), []
    for v in (b.get("VersionContent"), b.get("VersionLive"), b.get("VersionStage"), 0):
        try:
            v = int(v or 0)
        except (TypeError, ValueError):
            continue
        if v not in seen:
            seen.add(v)
            cands.append(v)
    for v in cands:
        # bundle paths can contain spaces (e.g. ".../ghost assets important/...") — encode them,
        # keeping '/' as path separators, the same way the client's UnityWebRequest does.
        url = f"{PROBE_CDN}{urllib.parse.quote(noext)}/{v}/{urllib.parse.quote(base)}"
        try:
            req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=10) as r:
                if r.status == 200:
                    return v
        except Exception:
            continue
    return None


def _resolve_bundles_upstream(ids):
    """Fetch unknown bundle defs from AE's API once, cache + persist them. Best-effort."""
    if not ids:
        return
    url = UPSTREAM + "data/GetAssetBundlesByIDs?ids=" + ",".join(str(i) for i in ids)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=20) as r:
            arr = json.loads(r.read().decode("utf-8"))
    except Exception as ex:
        print(f"  [api] bundle proxy FAIL ({len(ids)} ids): {ex}")
        return
    learned = 0
    for b in arr if isinstance(arr, list) else []:
        try:
            _bundles[int(b["ID"])] = b
            learned += 1
        except (KeyError, TypeError, ValueError):
            pass
    if learned:
        _save_bundle_registry()
        print(f"  [api] learned {learned} asset bundles (registry now {len(_bundles)})")


def get_asset_bundles(conn, qs):
    """data/GetAssetBundlesByIDs?ids=1,2,3 -> [AssetBundleData,...]. Returns EVERY requested id
    (resolved from our registry, proxied from AE on a miss, or a harmless stub) so the client's
    loader never throws/hangs."""
    ids = _ids(qs)
    missing = [i for i in ids if i not in _bundles]
    if missing:
        _resolve_bundles_upstream(missing)
    out = []
    probed = False
    for i in ids:
        b = _bundles.get(i)
        if b is None:
            out.append({"ID": i, "Name": "", "Filename": "",
                        "VersionContent": 0, "VersionStage": 0, "VersionLive": 0})
            continue
        # The client picks VersionContent/Stage/Live by build env, then builds a /{version}/ CDN
        # URL — wrong version => 404 => the asset (e.g. a cutscene) hangs forever. AE's metadata
        # versions don't reliably match what's on the CDN, so probe it once and cache the answer;
        # fall back to the max metadata version if the probe can't reach the CDN.
        v = b.get("_cdnvL")
        if v is None:
            v = _probe_cdn_version(b)
            if v is not None:
                b["_cdnvL"] = v
                probed = True
        if v is None:
            v = max(int(b.get("VersionContent") or 0), int(b.get("VersionStage") or 0),
                    int(b.get("VersionLive") or 0))
        out.append({**{k: val for k, val in b.items() if not k.startswith("_")},
                    "VersionContent": v, "VersionStage": v, "VersionLive": v})
    if probed:
        _save_bundle_registry()
    return out


_load_bundle_registry()


def login_nowinfinity(conn, form):
    """login/nowinfinity -> the launcher's account + server list + character bundle, built FRESH
    from the authenticated account/character — NO captured login template underneath.

    The client POSTs the TYPED username as form field `user` (UILoginActions.Login: CreateForm
    {user,pass,option,infinityVersion}), then sends the game server Login[const, account.unm,
    sToken] — i.e. the game-server username is whatever we put in `account.unm` here. So this is
    where per-player identity lives: game.build_account GET-OR-CREATEs that user's own character
    (game.login) and constructs the whole loginData (account block + CharSelect entry) from the DB,
    so each person logs in as themselves — never replaying the captured account's email/identity."""
    user = ""
    if isinstance(form, dict):
        user = (form.get("user", [""])[0] or "").strip()
    if not user:
        user = "Hero"
    pwd = (form.get("pass", [""])[0] or "") if isinstance(form, dict) else ""
    # Authenticate (or register) the account by username+password. A wrong password is rejected
    # here — this is the auth gate; the game server then trusts the session token we issue.
    char = game.login(conn, user, pwd)
    if char is None:
        print(f"  [api] login '{user}' -> REJECTED (bad password)")
        return {"bSuccess": False, "sMsg": "Invalid username or password."}
    token = game.issue_token(conn, char["account_id"])   # game-server Login must present this
    print(f"  [api] login '{user}' -> char#{char['id']} gold={char['gold']}")
    return {
        "bSuccess": True,
        "sMsg": "success",
        "account": game.build_account(conn, char, user, token),
        "bundles": {"Characters": CHARACTERS_BUNDLE},
        "Characters_Bundle": CHARACTERS_BUNDLE,
        "servers": [{
            "sName": "Infinity", "sIP": PUBLIC_HOST, "iPort": GAME_PORT,
            "iCount": 1, "iMax": 3000, "bOnline": True, "iChat": 2, "bUpg": False,
            "sLang": "en", "iLevel": 0, "accessLevel": 0,
        }],
    }


def infinity_vars(conn, qs):
    """Data/InfinityVars -> List<GameVar>. The client deserializes into a LIST, so this MUST be a
    JSON array — returning {} throws a JsonSerializationException (Player.log) and can stall flows
    that wait on it (e.g. cutscene loading). Empty list = no server game-vars defined yet."""
    return []


def get_soundtracks(conn, qs):
    """data/getsoundtracks?ids= -> soundtrack list (BGM + cutscene music), proxied from AE's
    public API. A cutscene frame can Load a music track and wait on it, so an empty stub left
    cutscenes hanging on "Soundtrack data could not be loaded". Best-effort; empty on failure
    (the client just stays silent)."""
    ids = _ids(qs)
    if not ids:
        return []
    url = UPSTREAM + "data/getsoundtracks?ids=" + ",".join(str(i) for i in ids)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=20) as r:
            arr = json.loads(r.read().decode("utf-8"))
        return arr if isinstance(arr, list) else []
    except Exception as ex:
        print(f"  [api] soundtrack proxy FAIL ({len(ids)} ids): {ex}")
        return []


def dialogger_save(conn, form):
    """tweak/DialoggerSave {id, json} -> the saved cutscene id as PLAIN TEXT.
    Empty id = create (next id); else update. json is the Dialogger_Data blob
    (stored verbatim incl. its &lt;/&gt; escaping; the client HtmlDecodes on load)."""
    idv = (form.get("id", [""])[0] or "").strip()
    raw = form.get("json", [""])[0]
    if idv.isdigit() and int(idv) > 0:
        cid = int(idv)
        conn.execute("INSERT INTO cutscenes(id, raw) VALUES(?,?) "
                     "ON CONFLICT(id) DO UPDATE SET raw=excluded.raw", (cid, raw))
    else:
        cid = int(conn.execute("SELECT COALESCE(MAX(id), 0) + 1 FROM cutscenes").fetchone()[0])
        conn.execute("INSERT INTO cutscenes(id, raw) VALUES(?,?)", (cid, raw))
    conn.commit()
    return str(cid)


def dialogger_load(conn, form):
    """tweak/DialoggerLoad {id} -> the stored Dialogger_Data JSON as PLAIN TEXT."""
    try:
        cid = int(form.get("id", ["0"])[0])
    except (ValueError, IndexError):
        return ""
    row = conn.execute("SELECT raw FROM cutscenes WHERE id=?", (cid,)).fetchone()
    return row["raw"] if row else ""


def create_new_apop(conn, form):
    """tweak/CreateNewApop {npcID} -> {"ID": n}: mint a blank apop owned by the
    NPC so the in-game '+' button works. The client reads only dictionary["ID"]."""
    try:
        npc_id = int(form.get("npcID", ["0"])[0])
    except (ValueError, IndexError):
        npc_id = 0
    row = conn.execute("SELECT COALESCE(MAX(apop_id), 5999) + 1 AS nxt FROM apops").fetchone()
    apop_id = int(row["nxt"])
    name = None
    nrow = conn.execute("SELECT name FROM monsters WHERE mon_id=?", (npc_id,)).fetchone()
    if nrow:
        name = nrow["name"]
    blank = {
        "ID": apop_id, "nextElementId": 10, "name": name or f"Apop {apop_id}",
        "bgImage": "", "bgImageScale": 1, "startingPanels": [1],
        "panels": [{
            "PID": 1, "name": name or "NPC", "autoButton": -1, "panelPosition": 1,
            "hidePanelBG": False, "addVerticalSpacing": 0, "noTween": False,
            "tweenFrom": "Right", "changeApopBG": "", "changeApopBGScale": 1,
            "restoreBGOnClose": False, "elements": [{
                "ID": 1, "type": "Bubble", "color": "#000000", "changeBoxColor": "#FFFFFF",
                "nameplateTextColor": "#FFFFFF", "nameplateBoxColor": "#000000",
                "text": "...", "hideLead": False, "borderless": False, "addNameplate": False,
                "changeNameplate": "", "changeSubtitle": "", "compactMode": False,
                "requirements": [], "reqCondition": "AND", "lockedMode": "Hide",
            }],
        }],
        "actors": [{"npcid": npc_id, "name": name or "", "subtitle": "", "targetNode": 0,
                    "side": "left", "xOffset": 0, "yOffset": 0, "scale": 1, "animation": ""}],
        "freezeClient": 0, "IsSingleAction": False,
    }
    conn.execute("INSERT INTO apops(apop_id, name, raw) VALUES(?,?,?)",
                 (apop_id, name, json.dumps(blank, separators=(",", ":"))))
    # Hint for the TCP /dbapop that follows (which carries only the apop id): which
    # apop+NPC was just made, so the game server can attach it to the right pad.
    db.kv_set(conn, "last_created_apop", json.dumps({"apop_id": apop_id, "npc_id": npc_id}))
    conn.commit()
    return {"ID": str(apop_id)}


# --- apop editor: list/load/save the apop documents the in-game WebEditButton points at -------
# The in-game pencil opens WebApiURL + "apop/Edit.aspx?ID=n" (a browser). We serve our own editor
# page there + these JSON endpoints so apops can be authored without touching raw JSON. apops.raw
# stays a document (nested panels/elements), so this is an editor, not a column promotion.

def apop_list(conn, qs):
    """apop/list -> [{ID,name}] for the editor's picker (all apops, id-sorted)."""
    return [{"ID": r["apop_id"], "name": r["name"] or f"Apop {r['apop_id']}"}
            for r in conn.execute("SELECT apop_id, name FROM apops ORDER BY apop_id")]


def apop_load(conn, qs):
    """apop/load?ID=n -> the full apop document, or {} if absent (the editor starts blank)."""
    try:
        aid = int(urllib.parse.parse_qs(qs).get("ID", ["0"])[0])
    except (ValueError, IndexError):
        return {}
    row = conn.execute("SELECT raw FROM apops WHERE apop_id=?", (aid,)).fetchone()
    if not row:
        return {}
    try:
        return json.loads(row["raw"])
    except Exception:
        return {}


def apop_npcs(conn, qs):
    """apop/npcs?q=text -> [{id,name}] NPC matches for the actor picker (cap 50)."""
    q = (urllib.parse.parse_qs(qs).get("q", [""])[0] or "").strip().lower()
    if q:
        rows = conn.execute(
            "SELECT mon_id, name FROM monsters WHERE LOWER(name) LIKE ? ORDER BY mon_id LIMIT 50",
            (f"%{q}%",)).fetchall()
    else:
        rows = conn.execute("SELECT mon_id, name FROM monsters ORDER BY mon_id LIMIT 50").fetchall()
    return [{"id": r["mon_id"], "name": r["name"] or ""} for r in rows]


def apop_save(conn, form):
    """apop/save {id, json} -> {"ok":True,"ID":n}. Empty/0 id mints the next apop id (>=6000);
    else updates that apop. Stores the full document verbatim + a denormalized name column."""
    idv = (form.get("id", [""])[0] or "").strip()
    raw = form.get("json", ["{}"])[0]
    try:
        doc = json.loads(raw)
    except Exception:
        return {"ok": False, "msg": "Invalid apop document."}
    if not isinstance(doc, dict):
        return {"ok": False, "msg": "Apop document must be an object."}
    if idv.isdigit() and int(idv) > 0:
        apop_id = int(idv)
    else:
        row = conn.execute("SELECT COALESCE(MAX(apop_id), 5999) + 1 AS nxt FROM apops").fetchone()
        apop_id = int(row["nxt"])
    doc["ID"] = apop_id
    name = (doc.get("name") or f"Apop {apop_id}").strip() or f"Apop {apop_id}"
    conn.execute(
        "INSERT INTO apops(apop_id, name, raw) VALUES(?,?,?) "
        "ON CONFLICT(apop_id) DO UPDATE SET name=excluded.name, raw=excluded.raw",
        (apop_id, name, json.dumps(doc, separators=(",", ":"))))
    conn.commit()
    return {"ok": True, "ID": apop_id}


# ---- quest editor (DB manager): a quest + its four normalized tables on one page ---------------

def quest_list(conn, qs):
    """quest/list -> [{ID,name}] for the editor's quest picker (id-sorted)."""
    return [{"ID": r["quest_id"], "name": r["name"] or f"Quest {r['quest_id']}"}
            for r in conn.execute("SELECT quest_id, name FROM quests ORDER BY quest_id")]


def quest_load(conn, qs):
    """quest/load?ID=n -> {quest, turnins, drops, refs, rewards}, or {} if the quest is absent."""
    try:
        qid = int(urllib.parse.parse_qs(qs).get("ID", ["0"])[0])
    except (ValueError, IndexError):
        return {}
    return game.quest_editor_data(conn, qid) or {}


def quest_items(conn, qs):
    """quest/items?q=text -> [{id,name}] catalog item matches for reward/turnin pickers (cap 50)."""
    q = (urllib.parse.parse_qs(qs).get("q", [""])[0] or "").strip().lower()
    if q.lstrip("-").isdigit():
        rows = conn.execute("SELECT item_id, name FROM items WHERE item_id=? LIMIT 50",
                            (int(q),)).fetchall()
    elif q:
        rows = conn.execute("SELECT item_id, name FROM items WHERE LOWER(name) LIKE ? "
                            "ORDER BY item_id LIMIT 50", (f"%{q}%",)).fetchall()
    else:
        rows = conn.execute("SELECT item_id, name FROM items ORDER BY item_id LIMIT 50").fetchall()
    return [{"id": r["item_id"], "name": r["name"] or ""} for r in rows]


def quest_save(conn, form):
    """quest/save {json} -> {"ok":True,"ID":n}; writes the quest + all four normalized tables."""
    try:
        payload = json.loads(form.get("json", ["{}"])[0])
    except Exception:
        return {"ok": False, "msg": "Invalid quest payload."}
    if not isinstance(payload, dict):
        return {"ok": False, "msg": "Quest payload must be an object."}
    try:
        return game.quest_editor_save(conn, payload)
    except Exception as ex:
        return {"ok": False, "msg": f"save failed: {ex}"}


def get_base_classes(conn, qs):
    """Data/GetBaseClasses -> {items, hairs, character_bundle}. Feeds char-create AND the /charedit
    hair list (CharacterCustomizationController.BuildHairLists fetches this and reads .hairs). Served
    from the `base_classes` kv catalog (seeded from AE's live endpoint); empty-but-valid if unset so
    the client's JsonConvert never throws."""
    row = conn.execute("SELECT v FROM kv WHERE k=?", ("base_classes",)).fetchone()
    if row and row["v"]:
        try:
            return json.loads(row["v"])
        except Exception:
            pass
    return {"items": [], "hairs": [], "character_bundle": None}


# path (lowercased, no query) -> (method, handler taking (conn, query_or_form))
ROUTES = {
    "data/getbaseclasses":   ("GET",  get_base_classes),
    "data/getmonsterdata":   ("GET",  get_monster_data),
    "data/getassetbundlesbyids": ("GET", get_asset_bundles),
    "data/infinityvars":     ("GET",  infinity_vars),
    "data/getsoundtracks":   ("GET",  get_soundtracks),
    "login/nowinfinity":     ("POST", login_nowinfinity),
    "tweak/createnewapop":   ("POST", create_new_apop),
    "tweak/dialoggersave":   ("POST", dialogger_save),
    "tweak/dialoggerload":   ("POST", dialogger_load),
    "apop/list":             ("GET",  apop_list),
    "apop/load":             ("GET",  apop_load),
    "apop/npcs":             ("GET",  apop_npcs),
    "apop/save":             ("POST", apop_save),
    "quest/list":            ("GET",  quest_list),
    "quest/load":            ("GET",  quest_load),
    "quest/monsters":        ("GET",  apop_npcs),     # same monster picker (id/name)
    "quest/items":           ("GET",  quest_items),
    "quest/save":            ("POST", quest_save),
}

# Editor pages (staff-gated) -> the HTML file served for each. The in-game pencil opens apop;
# the quest editor is opened from a browser at WebApiURL + "quest/Edit.aspx?ID=n".
EDITOR_PAGES = {"apop/edit.aspx": "apop_editor.html", "apop/edit": "apop_editor.html",
                "quest/edit.aspx": "quest_editor.html", "quest/edit": "quest_editor.html"}
EDITOR_PREFIXES = ("apop/", "quest/")

# The DB-manager menu (the hamburger nav shared by every editor page via /editor/nav.js). Add a
# new editor here and it appears in the menu everywhere. soon=True renders it greyed/disabled.
EDITOR_MENU = [
    {"label": "Quests", "url": "/quest/Edit.aspx"},
    {"label": "Apops / Dialog", "url": "/apop/Edit.aspx"},
    {"label": "Shops", "url": "", "soon": True},
    {"label": "Items", "url": "", "soon": True},
    {"label": "Monsters & Drops", "url": "", "soon": True},
]

def _login_html(nxt, error):
    """The staff login page — posts game credentials to /editor/login. nxt is pre-sanitised."""
    err = (f'<p class="err">{error}</p>' if error else "")
    return f"""<!doctype html><html lang=en><head><meta charset=utf-8>
<meta name=viewport content="width=device-width, initial-scale=1"><title>Staff Login</title>
<style>
  :root {{ --bg:#1d2127; --panel:#272c34; --panel2:#2f3640; --line:#3a4150; --ink:#e6e9ef;
          --muted:#9aa4b2; --accent:#5b8cff; --danger:#e08585; }}
  body {{ margin:0; min-height:100vh; display:flex; align-items:center; justify-content:center;
         font:14px/1.45 system-ui,Segoe UI,Roboto,sans-serif; background:var(--bg); color:var(--ink); }}
  form {{ background:var(--panel); border:1px solid var(--line); border-radius:12px; padding:26px;
         width:320px; }}
  h1 {{ font-size:16px; margin:0 0 4px; }} p.sub {{ color:var(--muted); margin:0 0 18px; font-size:13px; }}
  label {{ display:block; font-size:12px; color:var(--muted); margin:12px 0 4px; }}
  input {{ width:100%; box-sizing:border-box; font:inherit; color:var(--ink); background:var(--panel2);
          border:1px solid var(--line); border-radius:6px; padding:9px 10px; }}
  button {{ width:100%; margin-top:18px; padding:10px; font:inherit; font-weight:600; cursor:pointer;
           color:#fff; background:var(--accent); border:1px solid var(--accent); border-radius:6px; }}
  .err {{ color:var(--danger); font-size:13px; margin:10px 0 0; }}
</style></head><body>
<form method=post action="/editor/login">
  <h1>Staff Login</h1>
  <p class=sub>Sign in with your in-game account.</p>
  <label>Username</label><input name=user autofocus autocomplete=username>
  <label>Password</label><input name=pass type=password autocomplete=current-password>
  <input type=hidden name=next value="{nxt}">
  <button type=submit>Sign in</button>
  {err}
</form></body></html>"""


# The editor pages are served from sibling .html files so they're editable without code changes.
def _editor_html(filename):
    try:
        return (LOG_DIR / filename).read_text(encoding="utf-8")
    except OSError:
        return ("<!doctype html><meta charset=utf-8><title>Editor</title>"
                f"<p>{filename} is missing on the server.</p>")


def _editor_nav_js():
    """The shared DB-manager nav, injected by every editor page via <script src=/editor/nav.js>.
    Builds a hamburger menu (from EDITOR_MENU) into the page's <header> next to its title, plus a
    Log out link. Data-driven: a new editor in EDITOR_MENU shows up here on every page."""
    return ("(function(){\n"
            "var ITEMS=" + json.dumps(EDITOR_MENU) + ";\n"
            "var cur=location.pathname.toLowerCase();\n"
            "var wrap=document.createElement('span');\n"
            "wrap.style.cssText='position:relative;display:inline-block;vertical-align:middle;margin-right:12px;font:13px/1.4 system-ui,Segoe UI,Roboto,sans-serif;';\n"
            "var btn=document.createElement('button');\n"
            "btn.type='button';btn.textContent='\\u2630';btn.title='Editors';\n"
            "btn.style.cssText='font-size:18px;line-height:1;padding:6px 10px;cursor:pointer;color:#e6e9ef;background:#272c34;border:1px solid #3a4150;border-radius:8px;';\n"
            "var menu=document.createElement('div');\n"
            "menu.style.cssText='display:none;position:absolute;top:calc(100% + 6px);left:0;z-index:9999;min-width:215px;background:#272c34;border:1px solid #3a4150;border-radius:10px;padding:6px;box-shadow:0 10px 30px rgba(0,0,0,.5);';\n"
            "var t=document.createElement('div');t.textContent='Database Manager';\n"
            "t.style.cssText='font-size:11px;text-transform:uppercase;letter-spacing:.06em;color:#9aa4b2;padding:6px 9px 8px;';menu.appendChild(t);\n"
            "ITEMS.forEach(function(it){\n"
            " var active=it.url&&cur.indexOf(it.url.toLowerCase().split('?')[0])===0;\n"
            " var el=document.createElement(it.url?'a':'div');\n"
            " el.textContent=it.label+(it.soon?'  \\u2014 soon':'');\n"
            " el.style.cssText='display:block;padding:8px 10px;border-radius:7px;text-decoration:none;color:'+(it.soon?'#6b7280':'#e6e9ef')+';'+(it.url?'cursor:pointer;':'cursor:default;')+(active?'background:#34405a;':'');\n"
            " if(it.url){el.href=it.url;el.onmouseenter=function(){el.style.background='#5b8cff';el.style.color='#fff';};el.onmouseleave=function(){el.style.background=active?'#34405a':'';el.style.color=it.soon?'#6b7280':'#e6e9ef';};}\n"
            " menu.appendChild(el);});\n"
            "var hr=document.createElement('div');hr.style.cssText='border-top:1px solid #3a4150;margin:6px 4px;';menu.appendChild(hr);\n"
            "var out=document.createElement('a');out.textContent='Log out';out.href='/editor/logout';\n"
            "out.style.cssText='display:block;padding:8px 10px;border-radius:7px;text-decoration:none;color:#9aa4b2;cursor:pointer;';\n"
            "out.onmouseenter=function(){out.style.background='#c0504d';out.style.color='#fff';};out.onmouseleave=function(){out.style.background='';out.style.color='#9aa4b2';};menu.appendChild(out);\n"
            "btn.onclick=function(e){e.stopPropagation();menu.style.display=(menu.style.display==='none'?'block':'none');};\n"
            "document.addEventListener('click',function(){menu.style.display='none';});\n"
            "wrap.appendChild(btn);wrap.appendChild(menu);\n"
            "function mount(){if(document.getElementById('__edmenu'))return;wrap.id='__edmenu';\n"
            " var h=document.querySelector('header');\n"
            " if(h){h.style.position=h.style.position||'relative';h.insertBefore(wrap,h.firstChild);}\n"
            " else{wrap.style.position='fixed';wrap.style.top='10px';wrap.style.left='10px';wrap.style.zIndex='9999';document.body.appendChild(wrap);}}\n"
            "if(document.body)mount();else document.addEventListener('DOMContentLoaded',mount);\n"
            "})();")


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send_json(self, obj, code=200):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_text(self, text, code=200, ctype="text/plain; charset=utf-8"):
        body = (text or "").encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, html, code=200):
        body = (html or "").encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _route_key(self):
        return self.path.split("?", 1)[0].lstrip("/").lower()

    def _cookie(self, name):
        for part in (self.headers.get("Cookie", "") or "").split(";"):
            k, _, v = part.strip().partition("=")
            if k == name:
                return v
        return ""

    def _require_edit_auth(self):
        """Gate the editor pages + endpoints behind a signed game-account session cookie (issued by
        /editor/login, access-gated in _verify_session). Enforced in the app so it holds via Caddy
        or direct :8182. FAIL CLOSED if no signing secret. Unauthed: redirect a page GET to the
        login form; 401 a data endpoint. Returns True if authorized."""
        if not EDIT_SECRET:
            self._send_json({"error": "editor auth not configured (set INFINITY_EDIT_PASS)"}, 503)
            return False
        if _verify_session(self._cookie(EDIT_COOKIE)):
            return True
        if self.command == "GET" and self._route_key() in EDITOR_PAGES:
            self.send_response(302)
            self.send_header("Location", "/editor/login?next=" + urllib.parse.quote(self.path))
            self.send_header("Content-Length", "0")
            self.end_headers()
        else:
            self._send_json({"error": "not authorized"}, 401)
        return False

    def _editor_login(self, method, body):
        """GET -> login form; POST {user,pass,next} -> authenticate a REAL game account, gate by
        access_level, set the session cookie + redirect. Never creates accounts."""
        def safe_next(n):
            return n if (n or "").startswith("/") and not (n or "").startswith("//") \
                and '"' not in (n or "") else "/quest/Edit.aspx"
        if method == "GET":
            nxt = urllib.parse.parse_qs(self.path.split("?", 1)[1] if "?" in self.path else "")\
                .get("next", ["/quest/Edit.aspx"])[0]
            return self._send_html(_login_html(safe_next(nxt), ""))
        form = urllib.parse.parse_qs(body.decode("utf-8", "replace"))
        user = (form.get("user", [""])[0] or "").strip()
        pw = form.get("pass", [""])[0] or ""
        nxt = safe_next(form.get("next", ["/quest/Edit.aspx"])[0])
        conn = db.connect()
        try:
            res = game.authenticate(conn, user, pw)
        finally:
            conn.close()
        if not res:
            return self._send_html(_login_html(nxt, "Invalid username or password."), 401)
        if int(res["access"]) < EDIT_MIN_ACCESS:
            return self._send_html(
                _login_html(nxt, "That account doesn't have staff access."), 403)
        token = _sign_session(res["username"], res["access"])
        self.send_response(302)
        self.send_header("Set-Cookie", f"{EDIT_COOKIE}={token}; Path=/; HttpOnly; SameSite=Lax; "
                         f"Max-Age={EDIT_SESSION_SECS}")
        self.send_header("Location", nxt)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _editor_logout(self):
        self.send_response(302)
        self.send_header("Set-Cookie", f"{EDIT_COOKIE}=; Path=/; Max-Age=0")
        self.send_header("Location", "/editor/login")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        self._handle("GET")

    def do_POST(self):
        self._handle("POST")

    def _handle(self, method):
        key = self._route_key()
        qs = self.path.split("?", 1)[1] if "?" in self.path else ""
        body = b""
        if method == "POST":
            length = int(self.headers.get("Content-Length", 0) or 0)
            body = self.rfile.read(length) if length else b""

        # public auth endpoints (the login flow itself — NOT gated, else redirect loop)
        if key == "editor/login":
            return self._editor_login(method, body)
        if key == "editor/logout":
            return self._editor_logout()
        if key == "editor/nav.js" and method == "GET":   # shared hamburger nav (UI only, no data)
            return self._send_text(_editor_nav_js(), ctype="application/javascript; charset=utf-8")

        # staff gate: every editor page AND endpoint (apop/*, quest/*). key is already lowercased,
        # so a mixed-case path can't slip past. Enforced before any editor handler runs.
        if key in EDITOR_PAGES or any(key.startswith(p) for p in EDITOR_PREFIXES):
            if not self._require_edit_auth():
                return

        # the editor pages themselves (HTML), opened by the in-game pencil or a browser
        if method == "GET" and key in EDITOR_PAGES:
            return self._send_html(_editor_html(EDITOR_PAGES[key]))

        route = ROUTES.get(key)
        if route and route[0] == method:
            conn = db.connect()
            try:
                arg = urllib.parse.parse_qs(body.decode("utf-8", "replace")) if method == "POST" else qs
                result = route[1](conn, arg)
            finally:
                conn.close()
            print(f"  [api] {method} {key} -> served")
            # plain text for the Dialogger endpoints (id / raw JSON string), JSON otherwise
            return self._send_text(result) if isinstance(result, str) else self._send_json(result)

        # not implemented yet
        if CAPTURE_PROXY:
            return self._proxy_and_log(method, body)
        self._log(UNHANDLED_LOG, {"method": method, "path": self.path,
                                  "body": body.decode("utf-8", "replace")})
        print(f"  [api] {method} {key} -> UNHANDLED (stub)")
        self._send_json({} if method == "GET" else {})

    def _proxy_and_log(self, method, body):
        # our WebApiURL base maps onto AE's api/ base, so the path (with query +
        # original case) appends straight onto UPSTREAM.
        url = UPSTREAM + self.path.lstrip("/")
        try:
            req = urllib.request.Request(
                url, data=body if method == "POST" else None, method=method,
                headers={"User-Agent": "Mozilla/5.0", "Content-Type":
                         self.headers.get("Content-Type", "application/json")})
            with urllib.request.urlopen(req, timeout=30) as r:
                data = r.read()
            self._log(CAPTURE_LOG, {"method": method, "path": self.path,
                                    "req": body.decode("utf-8", "replace"),
                                    "status": r.status,
                                    "resp": data.decode("utf-8", "replace")[:200000]})
            print(f"  [api] {method} {self._route_key()} -> CAPTURED ({len(data)}B)")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        except Exception as ex:
            print(f"  [api] proxy FAIL {url}: {ex}")
            self._send_json({}, 502)

    @staticmethod
    def _log(path, obj):
        try:
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(obj) + "\n")
        except Exception:
            pass


class _Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True   # rebind immediately on restart (don't fail on a TIME_WAIT socket)
    daemon_threads = True        # don't let lingering request threads block shutdown


def main():
    db.init()
    mode = "CAPTURE+proxy" if CAPTURE_PROXY else "local-only"
    with _Server((HOST, PORT), Handler) as httpd:
        print(f"Web API on http://{HOST}:{PORT}/  ({mode}); "
              f"routes: {', '.join(sorted(ROUTES))}")
        httpd.serve_forever()


if __name__ == "__main__":
    main()
