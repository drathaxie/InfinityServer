"""
Seeding is INSERT-IF-ABSENT: a re-run (every service restart calls seed.run()) must NOT clobber
content that was edited in-game. The DB is the live source of truth; data/ is only the initial
baseline. This proves edits to apops / monsters / maps survive a re-seed.
"""
import db
import seed
import montemplates


def main():
    db.use_throwaway()
    seed.run()                                   # first seed populates the catalog from data/
    conn = db.connect()

    apop_id = conn.execute("SELECT apop_id FROM apops ORDER BY apop_id LIMIT 1").fetchone()["apop_id"]
    map_name = conn.execute("SELECT str_map_name FROM maps ORDER BY map_id LIMIT 1").fetchone()["str_map_name"]
    mon_id = conn.execute("SELECT mon_id FROM monsters ORDER BY mon_id LIMIT 1").fetchone()["mon_id"]
    assert conn.execute("SELECT COUNT(*) FROM shop_items WHERE shop_id=2722").fetchone()[0] > 1000, \
        "dev shop 2722 was not stocked"

    # edit catalog content in place, the way the in-game editors (CreateNewApop, NPC/pad editor,
    # map authoring) mutate the DB
    conn.execute("UPDATE apops SET raw='{\"edited\":1}' WHERE apop_id=?", (apop_id,))
    montemplates.store(conn, mon_id, {"MonID": mon_id, "strMonName": "EditedMon"}, replace=True)
    conn.execute("UPDATE maps SET doc='{\"area\":{\"DisplayName\":\"EditedMap\"}}' WHERE str_map_name=?", (map_name,))
    conn.commit()

    seed.run()                                   # re-seed - simulates a service restart
    conn = db.connect()

    assert conn.execute("SELECT raw FROM apops WHERE apop_id=?", (apop_id,)).fetchone()["raw"] == '{"edited":1}', \
        "apop edit clobbered by re-seed"
    assert montemplates.get(conn, mon_id)["strMonName"] == "EditedMon", \
        "monster edit clobbered by re-seed"
    assert "EditedMap" in conn.execute("SELECT doc FROM maps WHERE str_map_name=?", (map_name,)).fetchone()["doc"], \
        "map edit clobbered by re-seed"

    # and the catalog is still fully present (re-seed didn't drop the untouched rows)
    assert conn.execute("SELECT COUNT(*) FROM monsters").fetchone()[0] >= 410, "catalog intact after re-seed"

    print("seed OK: re-run is insert-if-absent - in-game edits to apops/monsters/maps survive a restart")
    print("ALL SEED TESTS PASSED")


if __name__ == "__main__":
    main()

