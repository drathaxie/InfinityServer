#!/usr/bin/env python3
"""Plan/copy a complete current SQLite database to an EMPTY PostgreSQL schema.

Default is read-only planning. --apply copies inside one transaction, validates every
table's contents and identity sequence, and commits only on success. Occupied targets
are rejected; restoration/replacement must use a separate empty database.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import sqlite3

import db


TABLES = re.findall(r"CREATE TABLE IF NOT EXISTS\s+(\w+)", db.SCHEMA)


def quoted(name):
    if not re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*", name):
        raise ValueError("Unexpected schema identifier")
    return '"' + name + '"'


def source_tables(src):
    return {row[0] for row in src.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}


def copy_order(src):
    tables = source_tables(src)
    if tables != set(TABLES):
        raise ValueError(f"Source schema differs: missing={sorted(set(TABLES)-tables)}, "
                         f"extra={sorted(tables-set(TABLES))}. Upgrade a backup of the source first.")
    if src.execute("PRAGMA foreign_key_check").fetchone():
        raise ValueError("Source contains broken foreign-key references")
    dependencies = {t: {r[2] for r in src.execute(f"PRAGMA foreign_key_list({quoted(t)})")
                        if r[2] != t} for t in tables}
    ordered = []
    while dependencies:
        ready = sorted(t for t, deps in dependencies.items() if not deps - set(ordered))
        if not ready:
            raise ValueError("Cyclic foreign keys require an explicit migration")
        for t in ready:
            ordered.append(t)
            dependencies.pop(t)
    return ordered


def columns(src, table):
    return [r[1] for r in src.execute(f"PRAGMA table_info({quoted(table)})")]


def row_digest(rows):
    def encode(value):
        if isinstance(value, (bytes, memoryview)):
            return {"bytes": bytes(value).hex()}
        if isinstance(value, float) and value.is_integer():
            return int(value)
        return value
    # Sort row hashes, preserving duplicates, so physical row order is irrelevant.
    hashes = sorted(hashlib.sha256(json.dumps([encode(row[i]) for i in range(len(row))],
                    separators=(",", ":"), ensure_ascii=True).encode()).digest() for row in rows)
    return hashlib.sha256(b"".join(hashes)).hexdigest()


def migrate(src, dst, apply=False):
    order = copy_order(src)
    present = {r[0] for r in dst.execute("SELECT tablename FROM pg_tables WHERE schemaname=current_schema()")}
    if present - set(order):
        raise ValueError(f"Destination has unrelated tables: {sorted(present-set(order))}")
    occupied = [t for t in present if dst.execute(f"SELECT 1 FROM {quoted(t)} LIMIT 1").fetchone()]
    if occupied:
        raise ValueError(f"Destination must be empty; populated tables: {sorted(occupied)}")
    counts = {t: src.execute(f"SELECT COUNT(*) FROM {quoted(t)}").fetchone()[0] for t in order}
    print(json.dumps({"mode": "apply" if apply else "plan", "tables": counts}, indent=2))
    if not apply:
        dst.rollback()
        return counts
    try:
        # Schema DDL is transactional on PostgreSQL. Do not invoke db.init's data migrations.
        dst.executescript(db._schema_sql())
        dst.execute("LOCK TABLE " + ",".join(quoted(t) for t in order) + " IN ACCESS EXCLUSIVE MODE")
        if any(dst.execute(f"SELECT 1 FROM {quoted(t)} LIMIT 1").fetchone() for t in order):
            raise ValueError("Destination changed after planning")
        # Include additive columns/indexes from the same initializer used by a fresh server.
        # The locked destination is empty; replace only its generated initialization markers.
        db.initialize_connection(dst)
        dst.execute("DELETE FROM kv")
        for table in order:
            cols = columns(src, table)
            if set(cols) != db._columns(dst, table):
                raise ValueError(f"Column mismatch for {table}; upgrade the source schema first")
            selected = ",".join(quoted(c) for c in cols)
            source = src.execute(f"SELECT {selected} FROM {quoted(table)}").fetchall()
            if source:
                dst.executemany(f"INSERT INTO {quoted(table)} ({selected}) VALUES "
                                f"({','.join('?' for _ in cols)})", source)
            copied = dst.execute(f"SELECT {selected} FROM {quoted(table)}").fetchall()
            if len(copied) != len(source) or row_digest(copied) != row_digest(source):
                raise ValueError(f"Content verification failed for {table}")
        identities = dst.execute("SELECT table_name,column_name FROM information_schema.columns "
                                 "WHERE table_schema=current_schema() AND is_identity='YES'").fetchall()
        for row in identities:
            table, col = row[0], row[1]
            maximum = dst.execute(f"SELECT MAX({quoted(col)}) FROM {quoted(table)}").fetchone()[0]
            if maximum is not None:
                dst.execute("SELECT setval(pg_get_serial_sequence(?, ?), ?)", (table, col, maximum))
        dst.commit()
    except Exception:
        dst.rollback()
        raise
    print(f"Migration verified: {sum(counts.values())} rows across {len(order)} tables")
    return counts


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=db.DB_PATH)
    parser.add_argument("--apply", action="store_true", help="copy to an empty destination after planning")
    args = parser.parse_args()
    if db.BACKEND != "postgres":
        parser.error("Set INFINITY_DB=postgres and INFINITY_PG_* for the empty target")
    src = sqlite3.connect(args.source.resolve().as_uri() + "?mode=ro", uri=True)
    dst = db.connect()
    try:
        src.execute("BEGIN")  # consistent snapshot even if the source file changes
        migrate(src, dst, apply=args.apply)
    finally:
        src.close()
        dst.close()


if __name__ == "__main__":
    main()
