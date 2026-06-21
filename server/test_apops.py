"""
Apop catalog is DB-backed and live-editable (the AE model): seed.run() loads data/apops.json
into the apops table, getApop serves from there, and CreateNewApop / DialoggerSave mutate it in
place. No CDN — the database is the authoritative, on-the-fly-editable source of truth.
"""
import json

import db
import seed
import game


def main():
    db.use_throwaway()
    db.init()
    seed.run()
    conn = db.connect()

    # the base catalog is seeded into the DB (not fetched from a CDN)
    n = conn.execute("SELECT COUNT(*) FROM apops").fetchone()[0]
    assert n >= 66, f"apop catalog must be seeded into the DB, got {n}"

    # a seeded apop is served as the JSON string the client json.loads itself
    aid = conn.execute("SELECT apop_id FROM apops ORDER BY apop_id LIMIT 1").fetchone()[0]
    out = game.load_apops(conn, [aid])
    assert str(aid) in out, "seeded apop served from the DB"
    assert isinstance(out[str(aid)], str) and isinstance(json.loads(out[str(aid)]), dict)

    # an unknown id returns a valid EMPTY apop (never absent/null): the client's
    # NPCButton.LoadButton derefs the apop before its null check, so a missing apop NREs and
    # the NPC renders as a 'ghost'. A parseable empty apop keeps GetApopData non-null.
    unk = game.load_apops(conn, [999999])
    assert "999999" in unk, "unknown apop id -> still served (empty stub, not null)"
    stub = json.loads(unk["999999"])
    assert stub["panels"] == [] and "ID" in stub, "stub apop is valid-but-empty"

    # in-game authoring edits the row in place (edit on the fly); load reflects it immediately
    conn.execute("INSERT INTO apops(apop_id, name, raw) VALUES(5001, 'Authored', ?) "
                 "ON CONFLICT(apop_id) DO UPDATE SET name=excluded.name, raw=excluded.raw",
                 (json.dumps({"ID": 5001, "name": "Authored"}),))
    conn.commit()
    assert json.loads(game.load_apops(conn, [5001])["5001"])["name"] == "Authored", \
        "in-game authored apop is served live"

    print("apops OK: DB-backed catalog seeded, served as JSON strings, live-editable in place")
    print("ALL APOP TESTS PASSED")


if __name__ == "__main__":
    main()
