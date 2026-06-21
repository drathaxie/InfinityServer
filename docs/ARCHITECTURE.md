# Architecture

How InfinityServer is put together, subsystem by subsystem. For the wire protocol and DB schema
details see [SCHEMA.md](SCHEMA.md); for player-facing/admin behavior see
[USER_MANUAL.md](USER_MANUAL.md); for fix status / known gaps see [AUDIT.md](AUDIT.md) and
[TODO.md](TODO.md).

## Big picture

```
            ┌───────────────── stock Unity client ─────────────────┐
            │   (UnityDoorstop + Harmony mod: mod/InfinityLoader)   │
            │   patches: server-list IP  +  WebApiURL  at runtime   │
            └───────┬───────────────────────────────────┬──────────┘
                    │ TCP 5588 (\0-framed JSON)          │ HTTP 8182 (HTTPS via Caddy)
              ┌─────▼─────────────┐               ┌──────▼───────────┐
              │  server/server.py │               │ server/webapi.py │
              │  asyncio dispatch │               │  server list,    │
              │  Cmd -> handler   │               │  GetMonsterData, │
              └─────┬─────────────┘               │  asset bundles   │
                    │                              └──────┬───────────┘
        ┌───────────┼───────────────────────────────────┐│
        ▼           ▼            ▼          ▼             ▼▼
     game.py    combat.py    world.py   forge.py    montemplates.py …
        └───────────┴────────────┬───────┴─────────────┘
                                 ▼
                          server/db.py  (SQLite | Postgres dialect layer)
                                 ▼
                          data/  ──seed.py──▶  DB   (idempotent, ON CONFLICT DO NOTHING)
```

## Game server — `server/server.py`

The asyncio TCP front door (**port 5588**). Reads `0x00`-delimited frames, parses the JSON, reads
the `Cmd` field, and dispatches to a handler. Handles the login/handshake sequence
(`defaultclasses → sEAct → initPlayer → loginResponse → statUpdate`), then per-session in-game
commands. Auth is gated by a session token minted at login; passwords are validated hashed. Most
of the per-command logic lives in `game.py`; combat-affecting commands defer to `combat.py`.

## In-game logic — `server/game.py`

Player/session objects, login identity (honors the typed username), inventory (stacking + dedupe),
quests (accept / objective progress / turn-in, per-character), shops, banking, emotes, dev/staff
commands, and the social commands. Dev access is `access_level 100`, allowlisted in
`data/dev_users.txt` and applied at login.

## Combat — `server/combat.py` + `server/patterns.py`

The reverse-engineered damage model: per-class element/resource model, stat- and weapon-term
damage, miss/dodge/crit/cleave rolls, aura DoT/HoT ticking, Dragon's-Bane-style conditional
multipliers, and a combat narration log. Stats and weapon damage are produced by the **gem/Pattern
engine** in `patterns.py` (gems are the source of stats). Loot drops route through `loot.py` into
the loot inventory.

## World & multiplayer — `server/world.py`

Area/cell/entity state and the `monBranch` placement payload sent on `AreaJoin`. Map docs come from
`maps.py` (served from the DB), entity placements from `placements.py`. Supports real room
instancing and shared kill rewards across players in a cell.

## Web API — `server/webapi.py`

HTTP/JSON (**port 8182**). Serves the server list (`sIP`/`iPort` env-driven for hosted repoint),
`data/GetMonsterData` (the monster catalog, used by both world spawns and the apop/dialog actor
portraits), and the **asset-bundle registry** (`data/GetAssetBundlesByIDs`) — unknown bundle IDs
are proxied once from AE's public CDN and accumulated into `data/asset_bundles.json` so the client
never hangs. Custom `.unity3d` content is served via a Caddy content mirror that reverse-proxies
AE's CDN for everything else.

## Content authoring — `forge.py`, apop/Dialogger tooling

Staff accounts (`access_level 100`) unlock in-game authoring: the Dialogger cutscene editor, the
apop (`+`) dialog editor, charedit, and the SkillForge panel. See [AUTHORING.md](AUTHORING.md).

## Data layer — `server/db.py` + `server/seed.py`

`db.py` is a dialect wrapper: the whole codebase talks to a `sqlite3.Connection`-shaped object
(`conn.execute(sql, params)` with `?` placeholders and row-by-name access); on Postgres it rewrites
`?`→`%s` and adapts rows. The backend is chosen once at import via **`INFINITY_DB`** (`sqlite`
default, `postgres` hosted). Tests isolate via `db.use_throwaway()`. `seed.py` builds the schema and
fills it from the versioned `data/` files, insert-if-absent so in-game edits survive restarts.
One-shot SQLite→Postgres copy: `migrate_to_pg.py` (TRUNCATE+reload — don't run against live).

## Client mod — `mod/InfinityLoader`

Self-contained Doorstop loader bundling AE's own Harmony (no MelonLoader). Harmony patches
(`docs/RedirectPatch.cs`, `LoginPatch.cs`, `ContentPatch.cs`) repoint the client and allow our
plain-HTTP/HTTPS API on Unity 6. An always-on packet logger writes captures for protocol work.

## Reference content — `data/`

Versioned JSON: `maps/`, `monsters/`, `apops/`, plus `items.json`, `shops.json`, `quests.json`,
`classes*.json`, `class_*`, `skill_*`, `cutscenes.json`. This is the reproducible seed — edit here
rather than writing the live DB directly when you can.
