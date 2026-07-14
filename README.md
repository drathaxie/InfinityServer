# InfinityServer

A reverse-engineered multiplayer backend implementing asynchronous networking, persistent game
state, protocol compatibility, combat simulation, content tooling, and production deployment for
**AQW: Infinity**, Artix Entertainment's Unity remake of AdventureQuest Worlds.

## Engineering highlights

- Reverse-engineered a Unity multiplayer client's raw TCP and HTTP/JSON protocols from client
  behavior, decompiled reference code, and structured packet captures.
- Built an authoritative asynchronous game server in Python with login, character persistence,
  inventory, quests, shops, loot, leveling, combat, multiplayer world state, and staff tooling.
- Designed a shared persistence layer supporting **PostgreSQL in hosted deployments** and
  **SQLite for zero-configuration local development and isolated tests**.
- Created a version-controlled content pipeline with idempotent database seeding, schema extraction,
  packet-import tooling, and in-game authoring interfaces.
- Integrated the stock Unity client through a C# **Unity Doorstop + Harmony** runtime module without
  redistributing or modifying the commercial client binary on disk.
- Deployed the game server and web API as separate Ubuntu services using systemd, PostgreSQL, Caddy,
  and HTTPS.
- Documented implementation gaps and validated combat behavior against captured traffic rather than
  presenting approximations as exact reproductions.

The system is approximately **90% complete** and has been deployed to a private cloud environment
for controlled testing. Login/authentication, character persistence, combat, quests, shops, loot,
leveling, multiplayer, content authoring, and the custom asset pipeline work end to end.

> **Research and game-preservation project.** This repository is intended for controlled local or
> private study using a legitimately obtained client. It is not intended for commercial operation,
> public hosting, or redistribution of Artix Entertainment's copyrighted assets.

See [`docs/PORTFOLIO.md`](docs/PORTFOLIO.md) for a hiring-manager-friendly case study,
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for the subsystem map, and
[`docs/SCHEMA.md`](docs/SCHEMA.md) for protocol and database details.

---

## How it works

The client speaks two protocols, and InfinityServer implements both:

| Channel | What it is | Server |
|---|---|---|
| **Game** | Raw TCP socket. UTF-8 JSON messages are terminated by a single `0x00` byte and dispatched by their `Cmd` field. | `server/server.py` — asyncio, port 5588 |
| **Web API** | HTTP/JSON for the server list, monster catalog, asset-bundle registry, and content definitions. | `server/webapi.py` — port 8182; HTTPS via Caddy when hosted |

A small client integration module built with Unity Doorstop and Harmony repoints the stock client at
these endpoints at runtime. Data is backed by PostgreSQL in hosted deployments and SQLite for local
development through one dialect layer in `server/db.py`.

---

## Repository layout

```text
server/          multiplayer backend
  server.py        asyncio TCP dispatch and command routing
  webapi.py        HTTP API: server list, monsters, asset bundles, content
  handlers/        command handlers grouped by domain
  game.py          player/session state and in-game systems
  combat.py        damage, skills, auras, miss/dodge/crit, monster combat
  patterns.py      gem/Pattern stat and weapon-damage engine
  forge.py         SkillForge and authored class/skill data
  world.py         area, room, cell, and entity state
  maps.py          map documents
  placements.py    authored entity placements
  montemplates.py  monster/NPC catalog
  loot.py          drop resolution and pending loot inventory
  db.py            SQLite/PostgreSQL dialect layer
  seed.py          idempotent database initialization from versioned data
  test_*.py        backend-independent pytest suite
data/            versioned maps, monsters, items, shops, quests, classes, and skills
capture/         extraction/import tooling used to build the content catalog
schema/          protocol schema extraction and generated command-field map
mod/             InfinityLoader — Doorstop + Harmony client integration
customBundles/   original custom `.unity3d` content managed with Git LFS
deploy/          systemd units and hosting runbooks
scripts/         database and deployment helper scripts
docs/            architecture, audits, schema, authoring, and operational documentation
```

`docs/decomp/` and raw packet captures are intentionally excluded. The server generates live state
from its database and does not replay captured account data.

---

## Run locally

No PostgreSQL server, cloud account, or secrets are required. SQLite is the default backend.

```sh
# Create an optional virtual environment
python -m venv .venv

# Windows
.venv\Scripts\activate

# macOS/Linux
# source .venv/bin/activate

pip install -r server/requirements.txt

# Start the game server; the SQLite database is created and seeded automatically
python server/server.py

# In another terminal, start the HTTP API
python server/webapi.py
```

To connect a locally installed client, build the integration module under `mod/` and follow
[`mod/README.md`](mod/README.md) and [`docs/USER_MANUAL.md`](docs/USER_MANUAL.md).

### Tests

```sh
pip install pytest
cd server
python -m pytest
```

The tests use a disposable database through `db.use_throwaway()`. PostgreSQL can also be exercised
locally by setting `INFINITY_DB=postgres` and the `INFINITY_PG_*` variables documented in
`server/.pg.env.example`.

---

## Hosted deployment

The private test deployment uses an Ubuntu VM with PostgreSQL bound locally, separate systemd units
for the TCP server and HTTP API, and Caddy for HTTPS termination. Secrets remain in gitignored
environment files on the host. See [`deploy/README.md`](deploy/README.md) for the deployment runbook.

---

## Responsible disclosure and repository safety

- Do not commit credentials, production environment files, raw packet captures, player data,
  decompiled commercial source, or third-party copyrighted assets.
- Report security concerns using the process in [`SECURITY.md`](SECURITY.md).
- This project is not affiliated with or endorsed by Artix Entertainment.

## Contributing

See [`CONTRIBUTING.md`](CONTRIBUTING.md) for development setup, branch and pull-request workflow,
tests, and code style.