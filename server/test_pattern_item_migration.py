"""Regression for converting disabled normal-inventory gem tokens into PatternItems."""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import db
import game
import patterns
import seed
from scripts import migrate_stranded_pattern_items as migration


def main():
    db.use_throwaway()
    seed.run()
    conn = db.connect()
    char = game.login(conn, "__pattern_migration__", "pw")
    gem = next(db.item(conn, r["item_id"]) for r in conn.execute(
        "SELECT item_id FROM items WHERE item_type=43 ORDER BY item_id"))

    # Reproduce the production bug: an ItemType 43 token persisted in char_items, with a
    # quantity greater than one and its Pattern incorrectly stored as an applied item pattern.
    gem["Quantity"] = 3
    bad_id = game._grant_item(conn, char["id"], gem)
    conn.commit()
    bad = conn.execute("SELECT * FROM char_items WHERE char_item_id=?", (bad_id,)).fetchone()
    assert bad and bad["quantity"] == 3 and bad["pattern_json"]

    rows = migration.stranded_rows(conn)
    data = migration.manifest(conn, rows)
    report = migration.summary(data)
    assert report["rows"] == 1 and report["gems"] == 3 and report["characters"] == 1

    result = migration.apply_migration(conn, data)
    assert result == {"inserted_patterns": 3, "deleted_char_item_rows": 1, "remaining": 0}
    assert conn.execute("SELECT 1 FROM char_items WHERE char_item_id=?", (bad_id,)).fetchone() is None
    loose = patterns.loose_gems(conn, char["id"])
    assert len(loose) == 3 and all(g["pattern"] == gem["ItemPattern"] for g in loose)

    # Idempotent after conversion: no stranded rows and an empty second apply changes nothing.
    data2 = migration.manifest(conn, migration.stranded_rows(conn))
    assert data2 == []
    assert migration.apply_migration(conn, data2) == {
        "inserted_patterns": 0, "deleted_char_item_rows": 0, "remaining": 0}
    print("pattern migration OK: 1 disabled row x3 -> 3 loose PatternItems; exact stats preserved")


if __name__ == "__main__":
    main()
