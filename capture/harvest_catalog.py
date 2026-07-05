#!/usr/bin/env python3
"""
Harvest the live AdventureQuest Worlds Infinity content catalog WITHOUT seeing it in-game.

The client discovers all content through plain, unauthenticated REST GETs keyed by
integer ID (see docs/decomp/AssetBundleDataLoader.cs, Main.cs). Definition lookups are
NOT gated by "has this character ever triggered it" — so we can enumerate the full ID
space and pull metadata for content that has never shipped to players.

The tell for "baked but not released" is in the data itself: every asset carries
VersionContent / VersionStage / VersionLive separately (AssetBundleData.cs). An asset
with VersionContent > 0 but VersionLive == 0 exists on the dev/content build but was
never pushed live.

This script is READ-ONLY: it fetches JSON metadata only. It does NOT download any
.unity3d bundle. Outputs land in capture/harvest/.

Endpoints (base = https://<env>.aq.com/game/api/):
    Data/GetBaseClasses                 -> full class roster
    data/GetAssetBundlesByIDs?ids=a,b,c -> [{ID,Name,Filename,VersionContent,
                                             VersionStage,VersionLive,Dirty}]

Usage:
    python harvest_catalog.py preflight              # validate endpoints + show shape
    python harvest_catalog.py classes                # GetBaseClasses across all 3 envs + diff
    python harvest_catalog.py bundles --max-id 60000 # sweep the bundle catalog
    python harvest_catalog.py all --max-id 60000
"""
import argparse, json, pathlib, time
import requests

ENVS = {
    "content": "https://contentinf.aq.com/game/",   # internal/dev build (unreleased lives here)
    "stage":   "https://stageinf.aq.com/game/",      # staging
    "live":    "https://infinity.aq.com/game/",       # what players get
}
DEFAULT_ENV = "live"
API = lambda base: base + "api/"

HERE = pathlib.Path(__file__).resolve().parent
OUT = HERE / "harvest"
OUT.mkdir(exist_ok=True)

# Mirror the Unity client: a plain GET, no auth header. Give a real UA so a WAF
# doesn't drop us, and identify ourselves honestly.
SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "InfinityServer-catalog-harvester/1.0 (UnityWebRequest-compatible)",
    "Accept": "application/json, text/plain, */*",
})

# A bundle ID we already know exists (Mage armor, from data/class_rigs.json) — used to
# confirm the endpoint is alive and learn the response shape before sweeping.
KNOWN_BUNDLE_ID = 15774


def get(url, timeout=20, retries=3, backoff=1.5):
    """GET with light retry. Returns (status_code, text, parsed_json_or_None)."""
    last = None
    for attempt in range(retries):
        try:
            r = SESSION.get(url, timeout=timeout)
            parsed = None
            ct = r.headers.get("Content-Type", "")
            if r.ok and ("json" in ct or r.text.strip()[:1] in "[{"):
                try:
                    parsed = r.json()
                except Exception:
                    parsed = None
            return r.status_code, r.text, parsed
        except requests.RequestException as e:
            last = e
            time.sleep(backoff * (attempt + 1))
    return None, f"<request failed: {last}>", None


# --------------------------------------------------------------------------------------
def preflight(env):
    """Hit one known bundle ID + GetBaseClasses on the chosen env; print raw shape."""
    base = API(ENVS[env])
    print(f"# preflight against {env}: {base}\n")

    url = f"{base}data/GetAssetBundlesByIDs?ids={KNOWN_BUNDLE_ID}"
    code, text, parsed = get(url)
    print(f"GET {url}\n  HTTP {code}")
    print("  body:", (text[:600] + ("…" if len(text) > 600 else "")) if text else "<empty>")
    print()

    url = f"{base}Data/GetBaseClasses"
    code, text, parsed = get(url)
    print(f"GET {url}\n  HTTP {code}")
    if parsed is not None:
        n = len(parsed) if isinstance(parsed, list) else "?"
        print(f"  parsed: {type(parsed).__name__}, {n} entries")
        sample = parsed[0] if isinstance(parsed, list) and parsed else parsed
        print("  first entry keys:", list(sample.keys()) if isinstance(sample, dict) else sample)
    print("  body:", (text[:600] + ("…" if len(text) > 600 else "")) if text else "<empty>")


# --------------------------------------------------------------------------------------
def harvest_classes():
    """GetBaseClasses on every env; save raw + a name/id diff highlighting env-only classes."""
    rosters = {}
    for env in ENVS:
        base = API(ENVS[env])
        code, text, parsed = get(f"{base}Data/GetBaseClasses")
        print(f"[{env:7}] GetBaseClasses -> HTTP {code}, "
              f"{len(parsed) if isinstance(parsed, list) else type(parsed).__name__}")
        if parsed is not None:
            (OUT / f"baseclasses_{env}.json").write_text(
                json.dumps(parsed, indent=2), encoding="utf-8")
            rosters[env] = parsed
        time.sleep(0.4)

    def keyset(roster):
        # GetBaseClasses returns {"items":[...], "hairs":[...], "character_bundle":...};
        # the class roster is under "items". Tolerate a bare list too.
        out = {}
        items = roster.get("items") if isinstance(roster, dict) else roster
        if isinstance(items, list):
            for c in items:
                if isinstance(c, dict):
                    k = c.get("ID") or c.get("id") or c.get("ClassID")
                    nm = c.get("Name") or c.get("name") or c.get("ClassName")
                    out[k] = nm
        return out

    if rosters:
        keys = {env: keyset(r) for env, r in rosters.items()}
        live_ids = set(keys.get("live", {}))
        diff = {}
        for env in ("content", "stage"):
            extra = {str(k): keys[env][k] for k in keys.get(env, {}) if k not in live_ids}
            if extra:
                diff[f"{env}_only_not_live"] = extra
        (OUT / "baseclasses_diff.json").write_text(json.dumps(diff, indent=2), encoding="utf-8")
        print("\n# classes present on content/stage but NOT live (unreleased):")
        print(json.dumps(diff, indent=2) if diff else "  (none — or GetBaseClasses needs auth)")


# --------------------------------------------------------------------------------------
def harvest_bundles(env, max_id, batch, delay):
    """Sweep data/GetAssetBundlesByIDs over 1..max_id; save full catalog + unreleased subset.

    The version fields are env-independent (each response carries all three), so a single
    host gives us enough to compute the released/unreleased flag.
    """
    base = API(ENVS[env])
    found = {}

    def fetch(ids, depth=0):
        """Fetch a list of IDs, bisecting on 404. The server 404s a whole batch if it
        contains certain poison IDs (a malformed record server-side) — NOT merely a
        missing one — so on 404 we split and retry halves down to singletons. A lone
        ID that still 404s is genuinely absent and is skipped."""
        url = f"{base}data/GetAssetBundlesByIDs?ids=" + ",".join(map(str, ids))
        code, text, parsed = get(url)
        if isinstance(parsed, list):
            for row in parsed:
                if isinstance(row, dict) and "ID" in row:
                    found[row["ID"]] = row
            time.sleep(delay)
            return
        if code == 404 and len(ids) > 1:
            mid = len(ids) // 2
            fetch(ids[:mid], depth + 1)
            fetch(ids[mid:], depth + 1)
            return
        # singleton 404 (absent) or hard error — drop it
        time.sleep(delay)

    print(f"# sweeping bundles 1..{max_id} via {env} in batches of {batch} "
          f"(delay {delay}s, bisecting on 404)")
    # No early-stop: the ID space has large gaps (armors ~15k, class particles ~78k),
    # so a run of empty batches does NOT mean the end. Sweep the full range.
    for start in range(1, max_id + 1, batch):
        ids = list(range(start, min(start + batch, max_id + 1)))
        before = len(found)
        fetch(ids)
        print(f"  ids {ids[0]:>6}-{ids[-1]:<6} -> +{len(found)-before:<4} rows "
              f"(total {len(found)})")

    catalog = sorted(found.values(), key=lambda r: r.get("ID", 0))
    (OUT / "bundles_catalog.json").write_text(json.dumps(catalog, indent=2), encoding="utf-8")

    def vint(r, k):
        try:
            return int(r.get(k) or 0)
        except Exception:
            return 0

    def fname(r):
        return r.get("FileName") or r.get("Filename") or ""

    # "Unreleased" = staged/content-built but never pushed to the live players' build.
    unreleased = [r for r in catalog
                  if vint(r, "VersionLive") == 0
                  and (vint(r, "VersionStage") > 0 or vint(r, "VersionContent") > 0)]
    (OUT / "bundles_unreleased.json").write_text(json.dumps(unreleased, indent=2), encoding="utf-8")

    print(f"\n# wrote {len(catalog)} bundles -> capture/harvest/bundles_catalog.json")
    print(f"# {len(unreleased)} look UNRELEASED (Live==0, Stage/Content>0) "
          f"-> capture/harvest/bundles_unreleased.json")

    # Tally unreleased by Type so the class/skill content jumps out.
    bytype = {}
    for r in unreleased:
        bytype.setdefault(r.get("Type", "?"), []).append(r)
    print("\n# unreleased by Type:", {k: len(v) for k, v in sorted(bytype.items())})

    classy = [r for r in unreleased
              if r.get("Type") == "CLASS"
              or any(s in (str(r.get("Name", "")) + fname(r)).lower()
                     for s in ("class", "skill", "aspect"))]
    print(f"\n# unreleased CLASS/skill-flavored ({len(classy)}):")
    for r in sorted(classy, key=lambda r: r.get("ID", 0)):
        print(f"  {r.get('ID'):>6}  [{r.get('Type','?'):8}] "
              f"S{vint(r,'VersionStage')}/L{vint(r,'VersionLive')}  "
              f"{r.get('Name','')!r:36}  {fname(r)}")


# --------------------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("mode", choices=["preflight", "classes", "bundles", "all"])
    ap.add_argument("--env", choices=list(ENVS), default=DEFAULT_ENV,
                    help="environment host for the bundle sweep (default: live)")
    ap.add_argument("--max-id", type=int, default=60000, help="highest bundle ID to probe")
    ap.add_argument("--batch", type=int, default=200, help="IDs per request")
    ap.add_argument("--delay", type=float, default=0.4, help="seconds between batches (be polite)")
    args = ap.parse_args()

    if args.mode == "preflight":
        preflight(args.env)
    elif args.mode == "classes":
        harvest_classes()
    elif args.mode == "bundles":
        harvest_bundles(args.env, args.max_id, args.batch, args.delay)
    elif args.mode == "all":
        harvest_classes()
        print()
        harvest_bundles(args.env, args.max_id, args.batch, args.delay)


if __name__ == "__main__":
    main()
