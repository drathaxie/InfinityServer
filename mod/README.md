# InfinityLoader — our self-contained AE:I client mod

A minimal client mod for the AdventureQuest Worlds: Infinity (Unity) playtest. It does two
things and nothing else:

1. **Redirects the web API** — Harmony-patches `Main.WebApiURL` to return our local server
   (`http://127.0.0.1:8182/`), so login, the server list, monster data and the dev tools all
   come from `server/webapi.py` instead of Artix. The server list webapi returns points the
   client at our game server (`127.0.0.1:5588`), so no socket/connect patch is needed.
2. **Always-on packet logger** — mirrors every c2s request and s2c response into
   `UserData/Beyond/packets.jsonl` (our capture ground truth), in the same format the old mod used.

## Why not MelonLoader?

AE:I ships its **own** HarmonyLib (`0Harmony.dll`, v2.4.2.0, a ~2.4 MB merged build) in
`…_Data/Managed/`. That collides with MelonLoader's bootstrap Harmony (it expects a standard
`0Harmony` with `HarmonyLib.Tools.Logger`) and MelonLoader fails to initialize with a
`TypeLoadException` before any mod loads. Rather than fight it, we inject with **Unity Doorstop**
and patch using **AE's own Harmony** — the exact instance the game already loaded, so there is no
version conflict. This is fully self-contained here; we don't depend on the external Beyond project.

## Layout

- `InfinityLoader/` — the loader assembly (netstandard2.1). `Doorstop.Entrypoint.Start()` applies
  the Harmony patches against AE's `Assembly-CSharp` + `0Harmony` (referenced from the game's
  Managed folder, `Private=False`).
- `doorstop/` — vendored Unity Doorstop v4.5.0 (x64): `winhttp.dll`, `.doorstop_version`, and our
  `doorstop_config.ini` (`target_assembly=InfinityLoader.dll`).
- `deploy.sh` — build + deploy into the game install (removes MelonLoader, copies Doorstop + the
  built loader, writes the `infinity_api.txt` redirect marker).

## Setup (after a fresh game install)

```bash
cd mod && ./deploy.sh          # game must be CLOSED
# then start the servers and launch the game:
cd ../server && python server.py & python webapi.py &
```

Asset bundles (maps, armors, monsters, cutscene art/audio) stream **directly from AE's public
CDN** — we host none of it (no mirror, no cache). Our webapi only resolves bundle IDs to their
CDN filenames (`data/GetAssetBundlesByIDs`); the client fetches the `.unity3d` from AE itself.

Toggle: the redirect is **opt-in** — it only fires while `UserData/infinity_api.txt` exists
(empty file = our default `:8182`; or put a base URL on the first line). Delete it (or set
`enabled=false` in `doorstop_config.ini`) to play live AE again. The packet logger is always on.

On launch, the loader writes `UserData/Beyond/infinity_loader.log` (which patches bound). If
`get_WebApiURL`/`AEC.*` are ever renamed by a game update, each patch is applied independently —
a failed logger patch never takes down the essential redirect.
