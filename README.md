# InfinityServer

A private-server emulator for **AQW: Infinity** — Artix Entertainment's Unity remake of
AdventureQuest Worlds — reverse-engineered from the decompiled client (`Assembly-CSharp.dll`)
and live packet captures.

> **Research / game-preservation project.** Emulating a live commercial game's server generally
> violates its ToS and may implicate content copyright. Run it against your own client, on your
> own machine, for study and preservation. Don't operate a public server.

The server is **~90% complete and runs in production** on a cloud VM: real login/auth, character
persistence, combat, quests, shops, loot, leveling, multiplayer, content authoring, and a custom
asset pipeline all work end-to-end.

---

## How it works

The client speaks two protocols, and we emulate both:

| Channel | What it is | Server |
|---|---|---|
| **Game** | Raw TCP socket, UTF-8 JSON messages each terminated by a single `0x00` byte. Every message has a `Cmd` field; the client dispatches s2c by `ResponseTypes.Get(cmd)`. Wire is **plaintext** (the binary's XOR `EncryptDecrypt` is never called). | `server/server.py` — asyncio, **port 5588** |
| **Web API** | HTTP/JSON — server list, monster catalog, asset-bundle registry, content defs. | `server/webapi.py` — **port 8182** (HTTPS via Caddy in prod) |

A small **client mod** (Unity [Doorstop](https://github.com/NeighTools/UnityDoorstop) +
Harmony) repoints the stock client at our two endpoints — no client binary is modified on disk;
the patches rewrite the server-list IP and the `WebApiURL` at runtime. Data is backed by
**Postgres** in production and **SQLite** for local dev (one dialect layer, `server/db.py`).

See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for the subsystem map and
[`docs/SCHEMA.md`](docs/SCHEMA.md) for the wire protocol / DB schema.

---

## Repo layout

```
server/        the emulator
  server.py      asyncio TCP dispatch (\0-framed JSON, Cmd routing)   [port 5588]
  webapi.py      HTTP API: server list, GetMonsterData, asset bundles  [port 8182]
  game.py        player/session state, in-game command handlers
  combat.py      damage model, auto-attack, skills, DoT/HoT, miss/dodge/crit
  patterns.py    gem/Pattern stat engine (stats + weapon damage)
  forge.py       SkillForge
  world.py       area / cell / entity world state + broadcast
  maps.py        map docs;  placements.py  entity placements
  montemplates.py monster catalog (raw + crawled AE defs)
  loot.py        drops -> loot inventory
  db.py          SQLite|Postgres dialect layer (INFINITY_DB)
  seed.py        idempotent DB seed from data/ (ON CONFLICT DO NOTHING)
  test_*.py      pytest suite (backend-agnostic via db.use_throwaway())
data/          versioned content seed (maps, monsters, apops, items, shops, classes, ...)
capture/       extract/import tooling that built the data/ catalog from packet captures
               (the server itself replays no captures — login/state are generated from the DB)
schema/        extract_schema.py -> schema.json (cmd -> field map for all 220 types)
mod/           InfinityLoader — Doorstop + Harmony client mod (C#)
deploy/        systemd units + hosting runbook
scripts/       DB access helpers (psql-over-SSH; tunnel for a GUI)
customBundles/ our own custom .unity3d content (e.g. the Tato NPC)   [Git LFS]
docs/          design docs, schema, user manual, deploy/runbooks
```

> `docs/decomp/` (the ~1300 decompiled client `.cs` files) is **gitignored** — it's Artix's
> copyrighted code. Obtain your own decomp out-of-band; the server doesn't depend on it at runtime.

---

## Run it locally

No Postgres, no cloud, no secrets required — the default backend is a local **SQLite** file.

```sh
# 1. (optional) virtualenv; only psycopg is needed, and only for the Postgres backend
python -m venv .venv && . .venv/Scripts/activate     # Windows: .venv\Scripts\activate
pip install -r server/requirements.txt               # SQLite mode needs nothing extra

# 2. start the game server (auto-seeds the SQLite DB on first run)
python server/server.py            # -> "InfinityServer listening on … (SQLite)" on :5588

# 3. start the web API (separate process)
python server/webapi.py            # -> "Web API on http://…:8182 (SQLite)"
```

`server/server.py` calls `seed.run()` at startup, which builds and idempotently fills the DB from
the versioned files under `data/`. To drive the **real client**, build the mod under `mod/` and add
the `UserData` marker files that point it at your endpoints — see [`mod/README.md`](mod/README.md)
and [`docs/USER_MANUAL.md`](docs/USER_MANUAL.md).

### Tests

```sh
pip install pytest
cd server && python -m pytest          # backend-agnostic; uses a throwaway DB
```

The suite runs green on both backends. To exercise Postgres locally, set
`INFINITY_DB=postgres` plus the `INFINITY_PG_*` vars (see `server/.pg.env.example`).

---

## Production (overview — no secrets here)

Production runs on a cloud (OCI) Ubuntu VM with **Postgres** (localhost-only), two **systemd**
services (`infinity-game` on 5588, `infinity-api` on 8182), and **Caddy** terminating HTTPS
(Let's Encrypt) in front of the API and content mirror. All credentials live **only** on the VM in
`server/.pg.env` (gitignored; template at [`server/.pg.env.example`](server/.pg.env.example)) and
are shared out-of-band. Deploy = `scp server/*.py` to the VM + `systemctl restart`; the service
re-runs the idempotent seed on boot. Full runbook: [`deploy/README.md`](deploy/README.md) and
[`docs/HANDOFF_OCI_POSTGRES.md`](docs/HANDOFF_OCI_POSTGRES.md). DB access for collaborators:
[`docs/DB_ACCESS.md`](docs/DB_ACCESS.md).

---

## Contributing

See [`CONTRIBUTING.md`](CONTRIBUTING.md) — branch + PR flow, dev setup, tests, and code style.
