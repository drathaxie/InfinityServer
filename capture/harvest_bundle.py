#!/usr/bin/env python3
"""
Download a live-AE asset bundle by ID and dump its animation art (AnimationClip /
AnimatorController names, plus sprite/texture names) so unshipped content can be
inspected and reused for custom class/skill work.

The client resolves a bundle's CDN URL as (Main.cs, AssetBundleLoader.cs):
    {BaseURL}assetbundles/windows/{Filename minus .unity3d}/{version}/{basename}.unity3d
where BaseURL is the env host (https://infinity.aq.com/game/ for live) and version is
env-specific (VersionLive / VersionStage / VersionContent from GetAssetBundlesByIDs).
This is a plain GET, no auth — confirmed against live AE.

Usage:
    python harvest_bundle.py <bundle_id> [--env live|stage|content] [--version N]
    python harvest_bundle.py 70955                      # Characters rig (181 AnimationClips)
    python harvest_bundle.py 15774 --env live            # Mage armor bundle

Looks up Filename/version from capture/harvest/bundles_catalog.json if present
(run harvest_catalog.py first); otherwise pass --filename/--version explicitly.

Downloads to capture/harvest/bundles/<id>_<name>.unity3d
Dumps to     capture/harvest/dumps/<id>_<name>.json  {clips, controllers, sprites, textures}
"""
import argparse, json, pathlib, sys
import requests
import UnityPy

ENVS = {
    "content": "https://contentinf.aq.com/game/",
    "stage":   "https://stageinf.aq.com/game/",
    "live":    "https://infinity.aq.com/game/",
}

HERE = pathlib.Path(__file__).resolve().parent
CATALOG = HERE / "harvest" / "bundles_catalog.json"
BUNDLES_DIR = HERE / "harvest" / "bundles"
DUMPS_DIR = HERE / "harvest" / "dumps"
BUNDLES_DIR.mkdir(parents=True, exist_ok=True)
DUMPS_DIR.mkdir(parents=True, exist_ok=True)

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "InfinityServer-catalog-harvester/1.0"})


def lookup_catalog(bundle_id):
    if not CATALOG.exists():
        return None
    cat = json.loads(CATALOG.read_text(encoding="utf-8"))
    for row in cat:
        if row.get("ID") == bundle_id:
            return row
    return None


def bundle_url(env, filename, version):
    base = ENVS[env]
    stem = filename[:-len(".unity3d")] if filename.endswith(".unity3d") else filename
    basename = filename.split("/")[-1]
    return f"{base}assetbundles/windows/{stem}/{version}/{basename}"


def download(bundle_id, env, filename, version, out_path):
    url = bundle_url(env, filename, version)
    print(f"GET {url}")
    r = SESSION.get(url, timeout=60)
    print(f"  HTTP {r.status_code}  {len(r.content)} bytes")
    if r.status_code != 200:
        print(f"  body: {r.text[:300]}")
        return False
    out_path.write_bytes(r.content)
    return True


def dump(path):
    env = UnityPy.load(str(path))
    out = {"clips": [], "controllers": [], "sprites": [], "textures": [], "gameobjects": []}
    for obj in env.objects:
        t = obj.type.name
        if t in ("AnimationClip", "AnimatorController", "Sprite", "Texture2D", "GameObject"):
            try:
                name = obj.read().m_Name
            except Exception:
                continue
            key = {"AnimationClip": "clips", "AnimatorController": "controllers",
                   "Sprite": "sprites", "Texture2D": "textures",
                   "GameObject": "gameobjects"}[t]
            out[key].append(name)
    for k in out:
        out[k] = sorted(set(out[k]))
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("bundle_id", type=int)
    ap.add_argument("--env", choices=list(ENVS), default="live")
    ap.add_argument("--filename", help="override FileName (e.g. gameassets/70955_characters.unity3d)")
    ap.add_argument("--version", type=int, help="override version number")
    ap.add_argument("--skip-download", action="store_true", help="dump only, if already downloaded")
    args = ap.parse_args()

    row = lookup_catalog(args.bundle_id)
    filename = args.filename or (row.get("FileName") or row.get("Filename") if row else None)
    version = args.version
    if version is None and row is not None:
        version = {"live": row.get("VersionLive"), "stage": row.get("VersionStage"),
                   "content": row.get("VersionContent")}[args.env]
    name = (row or {}).get("Name") or str(args.bundle_id)
    safe_name = "".join(c if c.isalnum() else "_" for c in name)

    if not filename or not version:
        print("No catalog entry and no --filename/--version given. "
              "Run harvest_catalog.py first, or pass both explicitly.", file=sys.stderr)
        sys.exit(1)

    out_bundle = BUNDLES_DIR / f"{args.bundle_id}_{safe_name}.unity3d"
    if not args.skip_download:
        ok = download(args.bundle_id, args.env, filename, version, out_bundle)
        if not ok:
            sys.exit(1)
    elif not out_bundle.exists():
        print(f"--skip-download given but {out_bundle} doesn't exist.", file=sys.stderr)
        sys.exit(1)

    result = dump(out_bundle)
    out_json = DUMPS_DIR / f"{args.bundle_id}_{safe_name}.json"
    out_json.write_text(json.dumps(result, indent=2), encoding="utf-8")

    print(f"\n{len(result['clips'])} AnimationClips, {len(result['controllers'])} AnimatorControllers, "
          f"{len(result['sprites'])} Sprites, {len(result['textures'])} Textures")
    print(f"-> {out_json}")
    if result["clips"]:
        print("\nclips:")
        for c in result["clips"]:
            print(" ", c)


if __name__ == "__main__":
    main()
