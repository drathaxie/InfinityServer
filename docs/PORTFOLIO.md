# InfinityServer — Engineering Case Study

## Project summary

InfinityServer is a reverse-engineered multiplayer backend built to reproduce the server-side
behavior expected by a commercial Unity game client. The project combines protocol analysis,
asynchronous networking, persistent data modeling, gameplay simulation, developer tooling, client
integration, testing, and Linux deployment.

The goal was not to replay captured responses. The server generates accounts, characters, world
state, combat results, rewards, and content responses live from its own database.

## Role and ownership

This project was designed, implemented, documented, deployed, and operated as an independent
engineering effort. Major responsibilities included:

- analyzing the Unity client and recorded network traffic;
- defining the TCP and HTTP compatibility layers;
- implementing the Python server and gameplay systems;
- designing the SQLite/PostgreSQL persistence model;
- building C# runtime integration for the stock client;
- creating content extraction, migration, authoring, and seed pipelines;
- deploying and troubleshooting the hosted environment;
- documenting known gaps and validating behavior against evidence.

## Technical stack

| Area | Technology |
|---|---|
| Game server | Python, asyncio, null-delimited JSON over TCP |
| Web API | Python, HTTP/JSON |
| Persistence | PostgreSQL, SQLite, SQL migration/seed tooling |
| Client integration | C#, Unity Doorstop, Harmony |
| Testing | pytest, disposable backend-independent databases |
| Deployment | Ubuntu, systemd, Caddy, HTTPS |
| Content tooling | Python extraction/import scripts, JSON catalogs, in-game authoring tools |

## Architecture

```text
                      Stock Unity client
                 Doorstop + Harmony integration
                         /              \
              TCP JSON /                \ HTTP/JSON
                       /                  \
          asyncio game server          web API
             command routing       catalogs and server list
                    \                   /
                     \                 /
                gameplay subsystems
       sessions, world, combat, quests, shops, loot
                          |
                  persistence layer
                  SQLite | PostgreSQL
                          |
                versioned content seed
```

The game and API processes are intentionally separate. Both use the same persistence and content
model, while the client integration changes endpoint selection at runtime rather than redistributing
or permanently modifying the game executable.

## Engineering challenges

### Reconstructing an undocumented protocol

The client communicates through UTF-8 JSON frames terminated by a null byte. Each payload contains a
command identifier used by the client or server dispatcher. The protocol was reconstructed from
client behavior, decompiled reference code, and packet captures.

A schema-extraction tool records known command and field shapes, while unknown commands are logged
for later investigation. This made the reverse-engineering process incremental rather than requiring
complete protocol knowledge before implementation.

### Building authoritative multiplayer state

The backend owns character state, inventory, monster health, combat outcomes, room membership,
rewards, and authored content. Clients send intent; the server validates and broadcasts resulting
state.

Special attention was required for disconnect cleanup, duplicate logins, shared room state, monster
AI, shared kill credit, pending loot, and per-session authorization.

### Supporting local development and hosted deployment

SQLite provides a zero-configuration local path and fast disposable test databases. PostgreSQL is
used for hosted multi-user operation. A dialect wrapper preserves one application-facing connection
interface and adapts placeholders and row access for PostgreSQL.

Versioned content is seeded idempotently so a clean environment can be reproduced without
overwriting edits already made through authoring tools.

### Reproducing combat without overstating fidelity

Combat behavior includes class resources, stat and weapon terms, hit resolution, miss/dodge/crit
outcomes, multi-target hitboxes, auras, damage-over-time and healing-over-time, monster attacks, and
conditional effects.

Where captured traffic or client behavior provides evidence, tests assert against it. Where exact
server formulas cannot be recovered, the implementation and audit documentation identify the result
as an approximation or design choice instead of claiming exact parity.

### Creating a usable content workflow

The project includes catalogs for maps, monsters, items, shops, quests, classes, skills, cutscenes,
and asset bundles. Extraction tools convert captured or reference data into versioned seed files.
Staff-only in-game tools support map placement, dialog, cutscene, and skill authoring.

Authorization is enforced on the server because a modified client can invoke commands that the normal
UI hides.

## Reliability and security decisions

- Passwords are stored as hashes, with legacy values upgraded during authentication.
- Login sessions use generated tokens rather than trusting a username alone.
- Privileged content mutations are gated centrally on the server.
- Raw packet captures and local databases are excluded because they may contain personal data.
- Credentials, private keys, environment files, and decompiled commercial source are gitignored.
- Tests run against disposable databases so development does not modify persistent data.
- Database reads used by the monster loop use a dedicated connection and explicitly close read
  transactions to avoid idle-in-transaction failures.

## Demonstrated skills

- Python backend development
- asynchronous socket programming
- protocol analysis and compatibility engineering
- relational database design
- PostgreSQL and SQLite
- authentication and authorization
- multiplayer state synchronization
- automated testing
- C# and Unity runtime integration
- Linux service deployment and operations
- technical documentation
- evidence-driven debugging

## Résumé-ready summary

> Built and deployed a Python multiplayer game backend compatible with a stock Unity client,
> including asynchronous TCP command routing, HTTP APIs, PostgreSQL persistence, authoritative combat
> and world state, content authoring tools, C# runtime integration, automated tests, and Ubuntu
> service deployment.

## Interview discussion points

1. Why was a null-delimited stream parser needed, and how should malformed or oversized frames be
   handled?
2. How does the backend prevent an older duplicate-login session from removing the newer session's
   world state during cleanup?
3. Why maintain SQLite and PostgreSQL support instead of requiring PostgreSQL for every contributor?
4. Which combat behaviors were directly supported by evidence, and which required approximations?
5. Why are privileged commands checked server-side even when only staff users can see their UI?
6. How does idempotent content seeding preserve authored changes while keeping environments
   reproducible?

## Legal and ethical scope

InfinityServer is a research and preservation case study intended for controlled testing with a
legitimately obtained client. It is not affiliated with Artix Entertainment and is not intended for
commercial operation, public hosting, or redistribution of copyrighted game assets.