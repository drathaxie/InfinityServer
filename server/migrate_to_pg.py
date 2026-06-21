#!/usr/bin/env python3
"""
One-shot data copy: data/infinity.db (SQLite) -> Postgres.

The SQLite file is the source of truth for both authored content (seeded) AND per-player
state, so copying it wholesale brings everything over — no seed.run() needed on the target.

Run with the target selected via env (same vars db.py reads):
    INFINITY_DB=postgres INFINITY_PG_HOST=... INFINITY_PG_PORT=... INFINITY_PG_DB=... \
    INFINITY_PG_USER=... INFINITY_PG_PASSWORD=...  python migrate_to_pg.py

Idempotent: ensures the schema (db.init), TRUNCATEs the target tables (RESTART IDENTITY
CASCADE), bulk-copies every table preserving ids, advances the accounts/characters identity
sequences past MAX(id), and asserts per-table row counts match. Safe to re-run.
"""
import os
import sqlite3
import sys

import db

# FK-safe insertion order: parents before children (mirrors the REFERENCES in db.SCHEMA).
TABLES = [
    "accounts", "characters", "items", "char_items", "shops", "shop_items",
    "monsters", "maps", "quests", "quest_turnins", "quest_rewards", "apops",
    "cutscenes", "classes", "class_skills", "skills", "kv", "map_pads",
    "pad_npcs", "map_state",
]
# Tables whose `id` is a GENERATED ... AS IDENTITY column: after loading explicit ids we must
# advance the backing sequence so the next auto-insert doesn't collide.
IDENTITY_TABLES = ["accounts", "characters"]


def _sqlite_src():
    conn = sqlite3.connect(db.DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _columns(src, table):
    return [r["name"] for r in src.execute(f"PRAGMA table_info({table})")]


def main():
    if db.BACKEND != "postgres":
        sys.exit("migrate_to_pg: set INFINITY_DB=postgres (+ INFINITY_PG_*) to choose the target")

    db.init()                                  # ensure the target schema exists (idempotent)
    src = _sqlite_src()
    dst = db.connect()                         # _PgConnection over psycopg
    raw = dst._raw                             # underlying psycopg connection

    with raw.cursor() as cur:
        cur.execute("TRUNCATE " + ", ".join(TABLES) + " RESTART IDENTITY CASCADE")

    total = 0
    print(f"copying {db.DB_PATH} -> postgres:{os.environ.get('INFINITY_PG_DB', 'infinity')}")
    for t in TABLES:
        cols = _columns(src, t)
        rows = src.execute(f"SELECT {','.join(cols)} FROM {t}").fetchall()
        if rows:
            placeholders = ",".join(["%s"] * len(cols))
            with raw.cursor() as cur:
                cur.executemany(
                    f"INSERT INTO {t} ({','.join(cols)}) VALUES ({placeholders})",
                    [tuple(r) for r in rows])
        n_src = len(rows)
        n_dst = dst.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        assert n_src == n_dst, f"{t}: source {n_src} != target {n_dst}"
        total += n_src
        print(f"  {t:16s} {n_src:6d} rows")

    # advance the identity sequences past the highest copied id (default initial is 1, which
    # would collide with the preserved ids on the next auto-insert otherwise).
    for t in IDENTITY_TABLES:
        mx = dst.execute(f"SELECT MAX(id) FROM {t}").fetchone()[0]
        if mx is not None:
            dst.execute("SELECT setval(pg_get_serial_sequence(?, 'id'), ?)", (t, mx))

    dst.commit()
    src.close()
    print(f"migration OK: {total} rows across {len(TABLES)} tables; identity sequences advanced")


if __name__ == "__main__":
    main()
