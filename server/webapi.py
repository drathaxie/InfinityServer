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
  GET  loginscreen/background.png       -> private-server login-screen art
  GET  mod/InfinityLoader.dll(.sha256)  -> verified client-mod self-update payload
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
import random
import re
import secrets
import time
import urllib.parse
import urllib.request

import db
import statues
import game
import montemplates
import placements
import questdb
import editor_enums
import account_manager
import support_manager

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

# Player account-manager sessions are separate from staff editor sessions. Reuse the same
# deployment secret by default, or isolate them with INFINITY_ACCOUNT_SECRET.
ACCOUNT_SECRET = (os.environ.get("INFINITY_ACCOUNT_SECRET") or
                  os.environ.get("INFINITY_EDIT_SECRET") or
                  os.environ.get("INFINITY_EDIT_PASS") or "").encode("utf-8")
ACCOUNT_SESSION_SECS = 2 * 3600
ACCOUNT_COOKIE = "infinity_account"


def _sign_session(username, access):
    """A signed, expiring session token: base64(payload).hmac. Stateless — no session table."""
    payload = {"u": username, "a": int(access), "csrf": secrets.token_urlsafe(24),
               "exp": int(time.time()) + EDIT_SESSION_SECS}
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


def _sign_account_session(account_id, username):
    payload = {"id": int(account_id), "u": username, "csrf": secrets.token_urlsafe(24),
               "exp": int(time.time()) + ACCOUNT_SESSION_SECS}
    body = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode().rstrip("=")
    sig = hmac.new(ACCOUNT_SECRET, body.encode(), hashlib.sha256).hexdigest()
    return f"{body}.{sig}"


def _verify_account_session(token):
    if not token or not ACCOUNT_SECRET or "." not in token:
        return None
    body, sig = token.rsplit(".", 1)
    expect = hmac.new(ACCOUNT_SECRET, body.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expect):
        return None
    try:
        payload = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
    except Exception:
        return None
    return payload if int(payload.get("exp", 0)) >= time.time() else None

LOG_DIR = pathlib.Path(__file__).resolve().parent
CAPTURE_LOG = LOG_DIR / "webapi_capture.jsonl"
UNHANDLED_LOG = LOG_DIR / "webapi_unhandled.jsonl"
LOGINSCREEN_BG_PATH = LOG_DIR.parent / "data" / "loginscreen_background.png"
MOD_DIR = LOG_DIR.parent / "data" / "mod"
MOD_DLL_PATH = MOD_DIR / "InfinityLoader.dll"
MOD_DLL_HASH_PATH = MOD_DIR / "InfinityLoader.dll.sha256"
CLIENT_PACK_PATH = MOD_DIR / "InfinityServer-Client.zip"

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
            _normalize_equipped_items(c)
            out.append(c)
    return out


def _normalize_equipped_items(mon):
    equipped = mon.get("equippedItems") if isinstance(mon, dict) else None
    if not equipped:
        if isinstance(mon, dict):
            mon["equippedItems"] = {}
        return
    if isinstance(equipped, dict):
        mon["equippedItems"] = {str(k): v for k, v in equipped.items()}
        return
    if isinstance(equipped, list):
        out = {}
        for item in equipped:
            if not isinstance(item, dict):
                continue
            spot = item.get("EquipSpot")
            if spot is None:
                spot = item.get("equipSpot")
            if spot is not None:
                out[str(spot)] = item
        mon["equippedItems"] = out
        return
    mon["equippedItems"] = {}


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
    that wait on it (e.g. cutscene loading). The stock infinityMessage controls the login MOTD;
    InfinityLoader reads infinityGameNewsHeading from the same response."""
    return [
        {"sInfo": "infinityMessage",
         "live": "Welcome to Infinity, our own private server build!",
         "test": "Welcome to Infinity, our own private server build!"},
        {"sInfo": "infinityGameNewsHeading",
         "live": "Infinity Server News", "test": "Infinity Server News"},
    ]


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


def _search_terms(value):
    return re.findall(r"[a-z0-9]+", (value or "").lower())


def _looks_like_background(name, link, load_type):
    if load_type == "bg":
        return True
    words = set(_search_terms(f"{name} {link}"))
    markers = {"bg", "background", "backdrop", "foreground", "fg", "scenery", "sky", "room"}
    return bool(words & markers) or any(part.endswith("bg") or part.startswith("bg") or part.startswith("fg") for part in words)


def cutscene_assets(conn, qs):
    """Rank reusable art references found in saved Dialogger setup frames."""
    if isinstance(qs, str):
        qs = urllib.parse.parse_qs(qs)
    query = (qs.get("q", [""])[0] or "").strip()
    kind = (qs.get("kind", ["all"])[0] or "all").lower()
    terms = _search_terms(query)
    found = {}
    for row in conn.execute("SELECT id, raw FROM cutscenes"):
        try:
            scene = json.loads(row["raw"])
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        frames = scene.get("frames") or []
        if not frames:
            continue
        scene_name = scene.get("cutsceneName") or f"Cutscene {row['id']}"
        for command in frames[0]:
            if not isinstance(command, str) or not command.startswith("Load{"):
                continue
            body = command[5:-1] if command.endswith("}") else command[5:]
            fields = body.split("|")
            if len(fields) < 3 or fields[2] not in ("actor", "bg"):
                continue
            link, load_type = fields[1], fields[2]
            raw_name = link.split(",", 1)[-1] if "," in link else link
            name = re.sub(r"[_-]+", " ", raw_name).strip() or f"Asset {fields[0]}"
            background = _looks_like_background(name, link, load_type)
            if kind == "background" and not background:
                continue
            if kind == "actor" and background:
                continue
            haystack = " ".join(_search_terms(f"{name} {link} {scene_name}"))
            if terms and not all(term in haystack for term in terms):
                continue
            lowered = name.lower()
            score = (1000 if lowered == query.lower() else 0)
            score += 300 if query and lowered.startswith(query.lower()) else 0
            score += sum(80 if any(word.startswith(term) for word in _search_terms(name)) else 20 for term in terms)
            score += 25 if background else 0
            key = (load_type, link.lower())
            candidate = {"type": load_type, "link": link, "name": name, "scene": scene_name,
                         "background": background, "score": score}
            if key not in found or score > found[key]["score"]:
                found[key] = candidate
    results = sorted(found.values(), key=lambda item: (-item["score"], item["name"].lower()))
    for item in results:
        item.pop("score", None)
    return results


def cutscene_npcs(conn, qs):
    if isinstance(qs, str):
        qs = urllib.parse.parse_qs(qs)
    query = (qs.get("q", [""])[0] or "").strip().lower()
    if query.isdigit():
        rows = conn.execute("SELECT mon_id AS id, name FROM monsters WHERE mon_id=?", (int(query),))
    else:
        rows = conn.execute("SELECT mon_id AS id, name FROM monsters WHERE lower(name) LIKE ? "
                            "ORDER BY CASE WHEN lower(name)=? THEN 0 WHEN lower(name) LIKE ? THEN 1 ELSE 2 END, name",
                            (f"%{query}%", query, f"{query}%"))
    return [{"id": row["id"], "name": row["name"] or f"NPC {row['id']}"} for row in rows]
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
    # Coerce any invalid `lockedMode` (RequirementLockType) before storing — one bad value bricks
    # the client's whole-batch getApop parse, so never let it persist (this blocked BattleOn).
    raw_out = game.sanitize_apop_raw(json.dumps(doc, separators=(",", ":")))
    conn.execute(
        "INSERT INTO apops(apop_id, name, raw) VALUES(?,?,?) "
        "ON CONFLICT(apop_id) DO UPDATE SET name=excluded.name, raw=excluded.raw",
        (apop_id, name, raw_out))
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


def item_list(conn, qs):
    """item/list -> [{ID,name}] for the item editor picker (id-sorted)."""
    return [{"ID": r["item_id"], "name": r["name"] or f"Item {r['item_id']}"}
            for r in conn.execute("SELECT item_id, name FROM items ORDER BY item_id")]


def item_load(conn, qs):
    """item/load?ID=n -> the item wire dict (generated from canonical columns), or {} if absent."""
    try:
        iid = int(urllib.parse.parse_qs(qs).get("ID", ["0"])[0])
    except (ValueError, IndexError):
        return {}
    return db.item(conn, iid) or {}


def item_save(conn, form):
    """item/save {json} -> {"ok":True,"ID":n}; upserts the item (replace) into the catalog columns.
    Unknown fields ride through `_extra`, so editing a curated set never drops data."""
    try:
        payload = json.loads(form.get("json", ["{}"])[0])
    except Exception:
        return {"ok": False, "msg": "Invalid item payload."}
    if not isinstance(payload, dict) or not str(payload.get("ID", "")).lstrip("-").isdigit():
        return {"ok": False, "msg": "Item needs a numeric ID."}
    try:
        db.store_item(conn, payload, replace=True)
        conn.commit()
        return {"ok": True, "ID": int(payload["ID"])}
    except Exception as ex:
        return {"ok": False, "msg": f"save failed: {ex}"}


def monster_list(conn, qs):
    """monster/list -> [{ID,name}] for the monster editor picker (id-sorted)."""
    return [{"ID": r["mon_id"], "name": r["name"] or f"Monster {r['mon_id']}"}
            for r in conn.execute("SELECT mon_id, name FROM monsters ORDER BY mon_id")]


def monster_load(conn, qs):
    """monster/load?ID=n -> {monster:{cols}, drops:[...]}, or {} if absent."""
    try:
        mid = int(urllib.parse.parse_qs(qs).get("ID", ["0"])[0])
    except (ValueError, IndexError):
        return {}
    return montemplates.editor_load(conn, mid) or {}


def monster_save(conn, form):
    """monster/save {json} -> {"ok":True,"ID":n}; writes the monster columns + its drop table."""
    try:
        payload = json.loads(form.get("json", ["{}"])[0])
    except Exception:
        return {"ok": False, "msg": "Invalid monster payload."}
    if not isinstance(payload, dict):
        return {"ok": False, "msg": "Monster payload must be an object."}
    try:
        return montemplates.editor_save(conn, payload)
    except Exception as ex:
        return {"ok": False, "msg": f"save failed: {ex}"}


def shop_list(conn, qs):
    """shop/list -> [{ID,name}] for the shop editor picker (id-sorted)."""
    return [{"ID": r["shop_id"], "name": r["name"] or f"Shop {r['shop_id']}"}
            for r in conn.execute("SELECT shop_id, name FROM shops ORDER BY shop_id")]


def shop_load(conn, qs):
    """shop/load?ID=n -> {shop:<full meta>, items:[{shop_item_id,item_id,cost,coins,quantity_remain,name}]}.
    The full meta is returned (incl. $type/gameFlag) so editing Name/Location preserves the rest."""
    try:
        sid = int(urllib.parse.parse_qs(qs).get("ID", ["0"])[0])
    except (ValueError, IndexError):
        return {}
    meta = db.shop_meta(conn, sid)
    if meta is None:
        return {}
    items = [{"shop_item_id": r["shop_item_id"], "item_id": r["item_id"], "cost": r["cost"],
              "coins": 1 if r["coins"] else 0, "quantity_remain": r["quantity_remain"],
              "name": r["name"] or ""}
             for r in conn.execute(
                 "SELECT si.shop_item_id, si.item_id, si.cost, si.coins, si.quantity_remain, i.name "
                 "FROM shop_items si LEFT JOIN items i ON i.item_id=si.item_id "
                 "WHERE si.shop_id=? ORDER BY si.shop_item_id", (sid,))]
    return {"shop": meta, "items": items}


def shop_save(conn, form):
    """shop/save {json} -> {"ok":True,"ID":n}; writes the shop meta + replaces its shop_items."""
    try:
        payload = json.loads(form.get("json", ["{}"])[0])
    except Exception:
        return {"ok": False, "msg": "Invalid shop payload."}
    shop = payload.get("shop") or {}
    try:
        sid = int(shop.get("shopID") if shop.get("shopID") is not None else shop.get("shop_id"))
    except (TypeError, ValueError):
        return {"ok": False, "msg": "Shop needs a numeric shopID."}
    try:
        db.store_shop(conn, shop, shop_id=sid, replace=True)     # preserves $type/gameFlag via _extra
        rows = payload.get("items") or []
        used = {int(it["shop_item_id"]) for it in rows if it.get("shop_item_id")}
        nxt = max(used) if used else 0
        conn.execute("DELETE FROM shop_items WHERE shop_id=?", (sid,))
        for it in rows:
            try:
                iid = int(it.get("item_id"))
            except (TypeError, ValueError):
                continue
            if conn.execute("SELECT 1 FROM items WHERE item_id=?", (iid,)).fetchone() is None:
                return {"ok": False, "msg": f"item {iid} isn't in the catalog (add it first)."}
            siid = it.get("shop_item_id")
            if not siid:
                nxt += 1
                while nxt in used:
                    nxt += 1
                siid = nxt
                used.add(siid)
            conn.execute("INSERT INTO shop_items(shop_id, shop_item_id, item_id, cost, coins, "
                         "quantity_remain) VALUES(?,?,?,?,?,?)",
                         (sid, int(siid), iid, int(it.get("cost", 0) or 0),
                          1 if int(it.get("coins", 0) or 0) else 0,
                          int(it.get("quantity_remain", -1) if it.get("quantity_remain") is not None else -1)))
        conn.commit()
        return {"ok": True, "ID": sid}
    except Exception as ex:
        return {"ok": False, "msg": f"save failed: {ex}"}


def map_list(conn, qs):
    """map/list -> [{ID,name}] of maps (ID = the map name string used everywhere as the key)."""
    return [{"ID": r["str_map_name"], "name": r["str_map_name"]}
            for r in conn.execute("SELECT str_map_name FROM maps ORDER BY str_map_name")]


def map_load(conn, qs):
    """map/load?map=X -> {map, authored, pads:[PadData...]}. pad_dict seeds the pads from the
    captured monBranch on first open (take_over), so the editor starts from the current NPCs."""
    mp = (urllib.parse.parse_qs(qs).get("map", [""])[0] or "").strip().lower()
    if not mp:
        return {}
    pads = placements.pad_dict(conn, mp)            # {pad_id: PadData} (auto-seeds + authors)
    return {"map": mp, "authored": placements.is_authored(conn, mp),
            "pads": [pads[k] for k in sorted(pads)]}


def map_save(conn, form):
    """map/save {json:{map, pads:[PadData...]}} -> {ok,ID}. Full-replace the map's pads+NPCs and
    keep it authored (so the served monBranch is compiled from these). Mirrors the in-game editor."""
    try:
        payload = json.loads(form.get("json", ["{}"])[0])
    except Exception:
        return {"ok": False, "msg": "Invalid map payload."}
    mp = (payload.get("map") or "").strip().lower()
    if not mp:
        return {"ok": False, "msg": "Map name required."}
    pads = payload.get("pads") or []
    try:
        conn.execute("DELETE FROM pad_npcs WHERE map=?", (mp,))
        conn.execute("DELETE FROM map_pads WHERE map=?", (mp,))
        for pad in pads:
            placements.write_pad(conn, mp, pad)
        conn.execute("INSERT INTO map_state(map, authored) VALUES(?,1) "
                     "ON CONFLICT(map) DO UPDATE SET authored=1", (mp,))
        conn.commit()
        return {"ok": True, "ID": mp, "msg": f"{len(pads)} pad(s) saved"}
    except Exception as ex:
        return {"ok": False, "msg": f"save failed: {ex}"}


def get_base_classes(conn, qs):
    """Data/GetBaseClasses -> {items, hairs, character_bundle}. Feeds char-create AND the /charedit
    hair list (CharacterCustomizationController.BuildHairLists fetches this and reads .hairs).
    `items`/`character_bundle` are served from the `base_classes` kv catalog (seeded from AE's live
    endpoint); `hairs` is generated from the hairs table (the full harvested roster, not just the
    24 AE's base-classes response carries) so it can't drift from what /charedit and HairShop use."""
    out = {"items": [], "hairs": [], "character_bundle": None}
    row = conn.execute("SELECT v FROM kv WHERE k=?", ("base_classes",)).fetchone()
    if row and row["v"]:
        try:
            blob = json.loads(row["v"])
            out["items"] = blob.get("items") or []
            out["character_bundle"] = blob.get("character_bundle")
        except Exception:
            pass
    out["hairs"] = db.hairs_list(conn)
    return out


# FounderTower pedestal world coords (parented under each frame's MapCell by the client mod).
# TUNE THESE HERE — editing + restarting infinity-api re-places the statues with NO client rebuild.
FOUNDER_STATUE_PPU = 88      # pixels-per-unit for tower statues (lower = larger). Tune live.
FOUNDER_PEDESTALS = [
    # Statue rooms R2/R3/R9. Coords are cell-local (parented under each MapCell). Starting anchors
    # = the Left/Right spawn pads pulled from the bundle; nudge x/y here per in-client feedback.
    # flip=True mirrors the statue to face the other way (right-column statues face inward/left).
    {"x": -31.0, "y": -4.25, "frame": "R2"},
    {"x":  31.0, "y": -4.25, "frame": "R2", "flip": True},
    {"x": -31.0, "y": -4.25, "frame": "R3"},
    {"x":  31.0, "y": -4.25, "frame": "R3", "flip": True},
    {"x": -31.0, "y": -4.25, "frame": "R9"},
    {"x":  31.0, "y": -4.25, "frame": "R9", "flip": True},
]


def founder_statues(conn, qs):
    """FounderTower pedestal roster + positions. Returns the pedestal world coords AND a RANDOMIZED
    subset (sized to the pedestal count) of the characters who have a generated Custom Hero Statue
    (statues.image present). Owners intentionally outnumber pedestals, so each visit shows a
    different random slice. `statues[i]` fills `pedestals[i]`; extra pedestals stay empty. Public
    game data, same as the statue PNGs themselves (no credentials in the payload)."""
    peds = FOUNDER_PEDESTALS
    rows = conn.execute(
        "SELECT s.char_id, c.name FROM statues s JOIN characters c ON c.id=s.char_id "
        "WHERE s.image IS NOT NULL ORDER BY s.char_id").fetchall()
    owners = [{"cid": int(r["char_id"]), "name": r["name"] or ""} for r in rows]
    random.shuffle(owners)
    # ppu = pixels-per-unit for the statue sprite (higher = smaller). Tune the tower statue size here.
    return {"statues": owners[:len(peds)], "pedestals": peds, "ppu": FOUNDER_STATUE_PPU,
            "total": len(owners)}


# path (lowercased, no query) -> (method, handler taking (conn, query_or_form))
ROUTES = {
    "data/getbaseclasses":   ("GET",  get_base_classes),
    "data/getmonsterdata":   ("GET",  get_monster_data),
    "data/getassetbundlesbyids": ("GET", get_asset_bundles),
    "data/infinityvars":     ("GET",  infinity_vars),
    "data/questdb":          ("GET",  questdb.get),
    "data/getsoundtracks":   ("GET",  get_soundtracks),
    "login/nowinfinity":     ("POST", login_nowinfinity),
    "tweak/createnewapop":   ("POST", create_new_apop),
    "tweak/dialoggersave":   ("POST", dialogger_save),
    "tweak/dialoggerload":   ("POST", dialogger_load),
    "tweak/csassets":       ("GET",  cutscene_assets),
    "tweak/csnpcs":         ("GET",  cutscene_npcs),
    "apop/list":             ("GET",  apop_list),
    "apop/load":             ("GET",  apop_load),
    "apop/npcs":             ("GET",  apop_npcs),
    "apop/save":             ("POST", apop_save),
    "quest/list":            ("GET",  quest_list),
    "quest/load":            ("GET",  quest_load),
    "quest/monsters":        ("GET",  apop_npcs),     # same monster picker (id/name)
    "quest/items":           ("GET",  quest_items),
    "quest/save":            ("POST", quest_save),
    "item/list":             ("GET",  item_list),
    "item/load":             ("GET",  item_load),
    "item/save":             ("POST", item_save),
    "monster/list":          ("GET",  monster_list),
    "monster/load":          ("GET",  monster_load),
    "monster/items":         ("GET",  quest_items),   # item picker for the drop table
    "monster/save":          ("POST", monster_save),
    "shop/list":             ("GET",  shop_list),
    "shop/load":             ("GET",  shop_load),
    "shop/items":            ("GET",  quest_items),   # item picker for the listing
    "shop/save":             ("POST", shop_save),
    "map/list":              ("GET",  map_list),
    "map/load":              ("GET",  map_load),
    "map/monsters":          ("GET",  apop_npcs),     # monster picker for placing NPCs
    "map/save":              ("POST", map_save),
    "founderstatues":        ("GET",  founder_statues),
}

# Editor pages (staff-gated) -> the HTML file served for each. The in-game pencil opens apop;
# the quest editor is opened from a browser at WebApiURL + "quest/Edit.aspx?ID=n".
EDITOR_PAGES = {"apop/edit.aspx": "apop_editor.html", "apop/edit": "apop_editor.html",
                "quest/edit.aspx": "quest_editor.html", "quest/edit": "quest_editor.html",
                "item/edit.aspx": "item_editor.html", "item/edit": "item_editor.html",
                "monster/edit.aspx": "monster_editor.html", "monster/edit": "monster_editor.html",
                "shop/edit.aspx": "shop_editor.html", "shop/edit": "shop_editor.html",
                "map/edit.aspx": "map_editor.html", "map/edit": "map_editor.html",
                "support/edit.aspx": "support_editor.html", "support/edit": "support_editor.html"}
EDITOR_PREFIXES = ("apop/", "quest/", "item/", "monster/", "shop/", "map/", "support/")

# The DB-manager menu (the hamburger nav shared by every editor page via /editor/nav.js). Add a
# new editor here and it appears in the menu everywhere. soon=True renders it greyed/disabled.
EDITOR_MENU = [
    {"label": "Quests", "url": "/quest/Edit.aspx"},
    {"label": "Apops / Dialog", "url": "/apop/Edit.aspx"},
    {"label": "Items", "url": "/item/Edit.aspx"},
    {"label": "Monsters & Drops", "url": "/monster/Edit.aspx"},
    {"label": "Shops", "url": "/shop/Edit.aspx"},
    {"label": "Maps & NPCs", "url": "/map/Edit.aspx"},
    {"label": "Player Support", "url": "/support/Edit.aspx"},
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

    def _send_json(self, obj, code=200, headers=None):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_bytes(self, body, code=200, ctype="application/octet-stream", disposition=None):
        body = body or b""
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store, max-age=0")
        if disposition:
            self.send_header("Content-Disposition", disposition)
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

    def _account_payload(self, body):
        try:
            return json.loads(body.decode("utf-8")) if body else {}
        except (ValueError, UnicodeDecodeError):
            return None

    def _heromart_auth(self, conn):
        """Resolve the stock client's Authorization: Bearer <charid>:<session-token>."""
        raw = (self.headers.get("Authorization", "") or "").strip()
        if not raw.lower().startswith("bearer "):
            return None
        identity = raw[7:].strip()
        cid, sep, token = identity.partition(":")
        if not sep or not cid.isdigit() or not token:
            return None
        account = conn.execute(
            "SELECT a.username FROM characters c JOIN accounts a ON a.id=c.account_id "
            "WHERE c.id=?", (int(cid),)).fetchone()
        char = game.resolve_session(conn, account["username"], token) if account else None
        return char if char is not None and int(char["id"]) == int(cid) else None

    def _heromart_api(self, method, key, qs):
        conn = db.connect()
        try:
            char = self._heromart_auth(conn)
            if char is None:
                return self._send_json({"success": False, "message": "Not authorized.",
                                        "rewardDesc": ""}, 401)
            if key == "webapi/heromart/recent" and method == "GET":
                return self._send_json(game.redeem_history(conn, char["account_id"]))
            if key == "webapi/heromart/redeemnow" and method == "POST":
                code = urllib.parse.parse_qs(qs).get("code", [""])[0]
                return self._send_json(game.redeem_code(conn, char, code))
            return self._send_json({"success": False, "message": "Not found.",
                                    "rewardDesc": ""}, 404)
        finally:
            conn.close()

    def _account_auth(self, require_csrf=False):
        session = _verify_account_session(self._cookie(ACCOUNT_COOKIE))
        if not session:
            self._send_json({"ok": False, "message": "Please sign in."}, 401)
            return None
        if require_csrf and not hmac.compare_digest(
                str(self.headers.get("X-CSRF-Token", "")), str(session.get("csrf", ""))):
            self._send_json({"ok": False, "message": "Your session could not be verified."}, 403)
            return None
        return session

    def _account_api(self, method, key, qs, body):
        if key == "account/api/login" and method == "POST":
            if not ACCOUNT_SECRET:
                return self._send_json({"ok": False, "message": "Account manager is not configured."}, 503)
            data = self._account_payload(body)
            if data is None:
                return self._send_json({"ok": False, "message": "Invalid request."}, 400)
            conn = db.connect()
            try:
                auth = game.authenticate(conn, (data.get("username") or "").strip(),
                                         data.get("password") or "")
                acc = (conn.execute("SELECT id,username FROM accounts WHERE LOWER(username)=LOWER(?)",
                                    ((data.get("username") or "").strip(),)).fetchone()
                       if auth else None)
            finally:
                conn.close()
            if not acc:
                return self._send_json({"ok": False, "message": "Invalid username or password."}, 401)
            conn = db.connect()
            try:
                conn.execute("UPDATE accounts SET last_accessed=? WHERE id=?", (time.time(), acc["id"]))
                conn.commit()
            finally:
                conn.close()
            token = _sign_account_session(acc["id"], acc["username"])
            setting = os.environ.get("INFINITY_COOKIE_SECURE")
            secure_on = (PUBLIC_HOST not in ("127.0.0.1", "localhost") if setting is None
                         else setting.lower() in ("1", "true", "yes"))
            secure = "; Secure" if secure_on else ""
            cookie = (f"{ACCOUNT_COOKIE}={token}; Path=/account; HttpOnly; SameSite=Strict; "
                      f"Max-Age={ACCOUNT_SESSION_SECS}{secure}")
            return self._send_json({"ok": True}, headers={"Set-Cookie": cookie})
        if key == "account/api/logout" and method == "POST":
            if not self._account_auth(require_csrf=True):
                return
            return self._send_json({"ok": True}, headers={"Set-Cookie":
                f"{ACCOUNT_COOKIE}=; Path=/account; HttpOnly; SameSite=Strict; Max-Age=0"})

        session = self._account_auth(require_csrf=method == "POST")
        if not session:
            return
        conn = db.connect()
        try:
            account = account_manager.account_for_session(conn, session["id"])
            if account is None:
                return self._send_json({"ok": False, "message": "Account no longer exists."}, 401)
            if key == "account/api/me" and method == "GET":
                return self._send_json({"ok": True, "csrf": session["csrf"],
                    "account": {"username": account["username"], "name": account["name"],
                                "level": int(account["level"]), "gold": int(account["gold"]),
                                "coins": int(account["coins"]),
                                "created": float(account["created"] or 0),
                                "lastAccessed": float(account["last_accessed"] or 0),
                                "upgradeDays": int(account["upgrade_days"] or 0),
                                "upgradeExpires": account["upgrade_expires"]},
                    "tokens": account_manager.token_inventory(conn, account["char_id"]),
                    "inventory": account_manager.inventory(conn, account["char_id"]),
                    "buybacks": account_manager.buyback_history(conn, session["id"]),
                    "friends": account_manager.friend_manager(conn, account["char_id"]),
                    "guild": account_manager.guild_manager(conn, account),
                    "houses": account_manager.house_manager(conn, account["char_id"]),
                    "tokenBalance": account_manager.token_balance(conn, account["char_id"]),
                    "redemptions": account_manager.redemption_history(conn, session["id"]),
                    "activity": support_manager.history(conn, session["id"], 50)})
            if key == "account/api/catalog" and method == "GET":
                args = urllib.parse.parse_qs(qs)
                return self._send_json({"ok": True, "items": account_manager.catalog(
                    conn, args.get("q", [""])[0], args.get("limit", ["5000"])[0])})
            data = self._account_payload(body)
            if data is None:
                return self._send_json({"ok": False, "message": "Invalid request."}, 400)
            if key == "account/api/username" and method == "POST":
                ok, message = account_manager.change_username(
                    conn, session["id"], data.get("currentPassword"), data.get("username"))
                return self._send_json({"ok": ok, "message": message}, 200 if ok else 400)
            if key == "account/api/password" and method == "POST":
                ok, message = account_manager.change_password(
                    conn, session["id"], data.get("currentPassword"), data.get("password"))
                return self._send_json({"ok": ok, "message": message}, 200 if ok else 400)
            if key == "account/api/sessions/revoke" and method == "POST":
                conn.execute("UPDATE accounts SET session_token=NULL WHERE id=?", (session["id"],))
                support_manager.audit(conn, session["id"], account["char_id"], "player", "self",
                                      "sessions_revoked", detail="Player revoked game sessions")
                conn.commit()
                return self._send_json({"ok": True, "message": "All game sessions were signed out."})
            if key == "account/api/redeem" and method == "POST":
                ok, message, item = account_manager.redeem(conn, session["id"], data.get("itemId"))
                return self._send_json({"ok": ok, "message": message,
                    "item": {"id": item.get("ID"), "name": item.get("Name")} if item else None,
                    "balance": account_manager.token_balance(conn, account["char_id"])},
                    200 if ok else 400)
            if key == "account/api/buyback" and method == "POST":
                ok, message, cost = account_manager.buy_back(
                    conn, session["id"], data.get("buybackId"), data.get("quantity", 1))
                return self._send_json({"ok": ok, "message": message, "cost": cost},
                                       200 if ok else 400)
            if key == "account/api/friend/remove" and method == "POST":
                ok, message = account_manager.remove_friend(
                    conn, account["char_id"], data.get("friendId"))
                return self._send_json({"ok": ok, "message": message}, 200 if ok else 400)
            if key == "account/api/guild/motd" and method == "POST":
                ok, message = account_manager.set_guild_motd(conn, account, data.get("motd"))
                return self._send_json({"ok": ok, "message": message}, 200 if ok else 400)
            if key == "account/api/guild/leave" and method == "POST":
                ok, message = account_manager.leave_guild(conn, account)
                return self._send_json({"ok": ok, "message": message}, 200 if ok else 400)
            if key == "account/api/house/equip" and method == "POST":
                ok, message = account_manager.equip_house(conn, account, data.get("itemId"))
                return self._send_json({"ok": ok, "message": message}, 200 if ok else 400)
        finally:
            conn.close()
        return self._send_json({"ok": False, "message": "Not found."}, 404)

    def _support_api(self, method, key, qs, body):
        staff = _verify_session(self._cookie(EDIT_COOKIE))
        if not staff:
            return self._send_json({"ok": False, "message": "Not authorized."}, 401)
        if method == "POST" and not hmac.compare_digest(
                str(self.headers.get("X-Staff-CSRF", "")), str(staff.get("csrf", ""))):
            return self._send_json({"ok": False, "message": "Staff session expired; sign in again."}, 403)
        conn = db.connect()
        try:
            args = urllib.parse.parse_qs(qs)
            if key == "support/session" and method == "GET":
                return self._send_json({"ok": True, "csrf": staff.get("csrf", ""),
                                        "staff": staff.get("u", "")})
            if key == "support/search" and method == "GET":
                return self._send_json({"ok": True, "players": support_manager.search_players(
                    conn, args.get("q", [""])[0])})
            if key == "support/player" and method == "GET":
                try:
                    obj = support_manager.player(conn, args.get("charId", [""])[0])
                except (TypeError, ValueError):
                    obj = None
                return self._send_json({"ok": bool(obj), "player": obj}, 200 if obj else 404)
            if key == "support/codes" and method == "GET":
                return self._send_json({"ok": True, "codes": support_manager.codes(conn)})
            data = self._account_payload(body)
            if data is None:
                return self._send_json({"ok": False, "message": "Invalid request."}, 400)
            if key == "support/grant" and method == "POST":
                ok, message = support_manager.grant(conn, staff.get("u", "staff"),
                    data.get("charId"), data.get("type"), data.get("value"),
                    data.get("quantity", 1), data.get("reason"))
                return self._send_json({"ok": ok, "message": message}, 200 if ok else 400)
            if key == "support/code/save" and method == "POST":
                try:
                    ok, message = support_manager.save_code(conn, staff.get("u", "staff"), data)
                except (TypeError, ValueError):
                    ok, message = False, "Invalid reward value."
                return self._send_json({"ok": ok, "message": message}, 200 if ok else 400)
            return self._send_json({"ok": False, "message": "Not found."}, 404)
        finally:
            conn.close()

    def do_GET(self):
        self._handle("GET")

    def do_POST(self):
        self._handle("POST")

    def do_PUT(self):
        self._handle("PUT")

    def _handle(self, method):
        key = self._route_key()
        qs = self.path.split("?", 1)[1] if "?" in self.path else ""
        body = b""
        if method in ("POST", "PUT"):
            length = int(self.headers.get("Content-Length", 0) or 0)
            if length > statues.MAX_RENDER_BYTES:
                return self._send_json({"error": "statue render is too large"}, 413)
            body = self.rfile.read(length) if length else b""

        if method == "GET" and key in ("account", "account/"):
            return self._send_html(_editor_html("account_manager.html"))
        if method == "GET" and key in ("account/prize", "account/prize/"):
            return self._send_html(_editor_html("prize_manager.html"))
        if key in ("webapi/heromart/recent", "webapi/heromart/redeemnow"):
            return self._heromart_api(method, key, qs)
        if key.startswith("account/api/"):
            return self._account_api(method, key, qs, body)
        if key.startswith("support/") and key not in EDITOR_PAGES:
            if not self._require_edit_auth():
                return
            return self._support_api(method, key, qs, body)


        # DynamicStatue's cid metadata resolves here. This is public game art, not
        # an editor endpoint; the saved snapshot contains no account credentials.
        # The current Unity client renders the fully assembled avatar locally, stone-grades
        # it, and uploads the transparent PNG. Authenticate against the same account token
        # used by the game socket; a client may only replace its own generated statue.
        if method == "PUT" and key == "statue/upload":
            cid = (self.headers.get("ccid", "") or "").strip()
            token = (self.headers.get("token", "") or "").strip()
            if not cid.isdigit() or not token:
                return self._send_json({"error": "missing statue identity"}, 400)
            conn = db.connect()
            try:
                account = conn.execute(
                    "SELECT a.username FROM characters c JOIN accounts a ON a.id=c.account_id "
                    "WHERE c.id=?", (int(cid),)).fetchone()
                char = (game.resolve_session(conn, account["username"], token)
                        if account is not None else None)
                is_self = char is not None and int(char["id"]) == int(cid)
                # Staff bypass: the /genstatues batch tool renders statues for OTHER characters and
                # uploads them with the DEV's own session token. Allow it when the token belongs to a
                # staff account, and force-create the statue row (the target never ran generateStatue).
                staff = conn.execute(
                    "SELECT MAX(c.access_level) AS a FROM accounts ac "
                    "JOIN characters c ON c.account_id=ac.id WHERE ac.session_token=?",
                    (token,)).fetchone()
                is_staff = bool(staff and int(staff["a"] or 0) >= game.DEV_ACCESS_LEVEL)
                if not is_self and not is_staff:
                    return self._send_json({"error": "not authorized"}, 401)
                ok = (statues.store_render(conn, int(cid), body) if is_self
                      else statues.store_render_force(conn, int(cid), body))
                if not ok:
                    return self._send_json({"error": "generate the statue before uploading"}, 409)
                conn.commit()
            finally:
                conn.close()
            print(f"  [api] PUT statue/upload -> stored cid={int(cid)} ({len(body)}B)")
            return self._send_json({"ok": True})

        if method == "GET" and key.startswith("statue/") and key.endswith(".png"):
            download = key.endswith("/download.png")
            cid = key[len("statue/"):-len("/download.png")] if download else key[len("statue/"):-4]
            if not cid.isdigit():
                return self._send_bytes(b"", 404, "image/png")
            conn = db.connect()
            try:
                image = statues.render_png(conn, int(cid))
            finally:
                conn.close()
            disposition = (f'attachment; filename="infinity-statue-{int(cid)}.png"'
                           if download and image else None)
            return self._send_bytes(image, 200 if image else 404, "image/png", disposition)

        if method == "GET" and key == "loginscreen/background.png":
            try:
                data = LOGINSCREEN_BG_PATH.read_bytes()
            except FileNotFoundError:
                data = None
            return self._send_bytes(data, 200 if data else 404, "image/png")

        if method == "GET" and key == "mod/infinityloader.dll.sha256":
            try:
                text = MOD_DLL_HASH_PATH.read_text(encoding="utf-8").strip()
            except FileNotFoundError:
                text = ""
            return self._send_text(text, 200 if text else 404)

        if method == "GET" and key == "mod/infinityloader.dll":
            try:
                data = MOD_DLL_PATH.read_bytes()
            except FileNotFoundError:
                data = None
            return self._send_bytes(data, 200 if data else 404, "application/octet-stream")

        if method == "GET" and key == "client/infinityserver-client.zip":
            try:
                data = CLIENT_PACK_PATH.read_bytes()
            except FileNotFoundError:
                data = None
            return self._send_bytes(data, 200 if data else 404, "application/zip",
                                    'attachment; filename="InfinityServer-Client.zip"')

        # public auth endpoints (the login flow itself — NOT gated, else redirect loop)
        if key == "editor/login":
            return self._editor_login(method, body)
        if key == "editor/logout":
            return self._editor_logout()
        if key == "editor/nav.js" and method == "GET":   # shared hamburger nav (UI only, no data)
            return self._send_text(_editor_nav_js(), ctype="application/javascript; charset=utf-8")
        if key == "editor/enums.json" and method == "GET":   # shared enum labels for dropdowns
            return self._send_text(json.dumps(editor_enums.ENUMS), ctype="application/json; charset=utf-8")

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
                reload_type = {"tweak/createnewapop": "apop", "apop/save": "apop",
                               "tweak/dialoggersave": "dialog",
                               "quest/save": "quest"}.get(key)
                if reload_type and (not isinstance(result, dict) or result.get("ok", True)):
                    db.kv_set(conn, "cache_revision:" + reload_type, time.time_ns())
                    conn.commit()
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
