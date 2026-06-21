# Accessing the live database

The production database is **Postgres on the OCI VM** (`130.162.189.229`, DB `infinity`).
It listens **only on the VM's localhost** (`127.0.0.1:5432`) and is firewalled off from the
internet — that's deliberate. You never connect to it directly; you reach it *through* the VM
over SSH. Two ways, depending on whether you want a terminal or a GUI.

> All paths below assume the SSH key at `C:\Users\jesse\Downloads\ssh-key-2026-06-19.key`.
> Override with `$env:INFINITY_SSH_KEY` / `$env:INFINITY_VM` if it moves.

---

## 1. Quick queries from the terminal — `scripts/db.ps1`

Nothing to install (it runs `psql` *on the VM*). From the repo root in PowerShell:

```powershell
# interactive psql shell (\dt list tables, \d characters describe, \q quit)
./scripts/db.ps1

# one-off query
./scripts/db.ps1 "SELECT id,name,gold,access_level FROM characters ORDER BY id"

# run a .sql file
./scripts/db.ps1 -File .\scripts\sql\whoami.sql

# CSV export (redirect to a file)
./scripts/db.ps1 -Csv "SELECT * FROM characters" > chars.csv
```

Git-bash / WSL equivalent: `./scripts/db.sh "SELECT ..."`, `./scripts/db.sh -f file.sql`,
`./scripts/db.sh --csv "..."`, or no args for the interactive shell.

`UPDATE`/`INSERT`/`DELETE` work the same way — you're hitting the **live** DB, so it takes effect
immediately (no deploy/restart needed for data changes). Example, grant gold + dev access:

```powershell
./scripts/db.ps1 "UPDATE characters SET gold=100000000, access_level=100 WHERE lower(name)='redux'"
```

⚠️ A player who is **currently logged in** keeps their old values until they relog — most state
(gold, access level, equipped class) is pushed to the client at login. Edit, then have them relog.

---

## 2. A real table UI (DBeaver / pgAdmin / TablePlus / DataGrip) — `scripts/db-tunnel.ps1`

```powershell
./scripts/db-tunnel.ps1        # forwards localhost:5433 -> the VM's Postgres. Leave it open.
```

Then create a Postgres connection in your GUI:

| Field | Value |
|-------|-------|
| Host | `127.0.0.1` |
| Port | `5433` |
| Database | `infinity` |
| User | `infinity` |
| Password | `INFINITY_PG_PASSWORD` from `/opt/infinity/server/.pg.env` on the VM |

To read the password: `./scripts/db.ps1` won't print it, but you can grab it directly —
`ssh -i <key> ubuntu@130.162.189.229 'grep PG_PASSWORD /opt/infinity/server/.pg.env'`.

---

## The tables (23)

Catalog (shared "what exists"): `items`, `monsters`, `maps`, `quests`, `quest_turnins`,
`quest_rewards`, `shops`, `shop_items`, `classes`, `skills`, `class_skills`.
Authored world (our content): `apops`, `cutscenes`, `pad_npcs`, `map_pads`, `map_state`.
Accounts / per-character state: `accounts`, `characters`, `char_items`, `char_quests`,
`char_quest_objectives`, `char_houses`. Plus `kv` (misc key/value).

Run `\d <table>` in the interactive shell to see columns. The design rationale lives in
[SCHEMA.md](SCHEMA.md); authoring content (NPCs, dialog, skills) is in [AUTHORING.md](AUTHORING.md).

---

## Notes & gotchas

- **`.pg.env` has two password vars.** `INFINITY_PG_PASSWORD` is the real one the server and these
  scripts use. `INFINITY_PG_PASS` is stale/unused — ignore it.
- **Don't run `migrate_to_pg.py` against prod** — it TRUNCATEs and reloads from local seed files,
  wiping live edits. `db.init()` is the safe, non-destructive one (CREATE TABLE IF NOT EXISTS).
- **Backups:** `ssh -i <key> ubuntu@130.162.189.229 'cd /opt/infinity/server && set -a && . ./.pg.env && set +a && PGPASSWORD=$INFINITY_PG_PASSWORD pg_dump -h 127.0.0.1 -U infinity infinity' > backup.sql`
