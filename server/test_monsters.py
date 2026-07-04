"""
Monster catalog is DB-backed (the AE model): seed.run() loads BOTH the captured monBranches
(data/maps) and AE's crawled GetMonsterData defs (data/monsters) into the monsters table, which
is the authoritative, editable store. No CDN.

- montemplates.catalog() returns AE's GetMonsterData def (1=1) from the `catalog` column.
- montemplates.get() returns a spawnable monBranch from `raw` - captured-rich where we have it,
  or derived from the crawled def for monsters we never captured in a map.
"""

import db
import seed
import montemplates


def main():
    db.use_throwaway()
    db.init()
    seed.run()
    conn = db.connect()

    # the FULL AE catalog (~410) is DB-resident now, not just the ~177 captured
    total = conn.execute("SELECT COUNT(*) FROM monsters").fetchone()[0]
    assert total >= 410, f"full monster catalog seeded into the DB, got {total}"
    with_cat = sum(1 for r in conn.execute("SELECT mon_id FROM monsters").fetchall()
                   if montemplates.catalog(conn, r["mon_id"]) is not None)
    assert with_cat >= 410, f"every crawled monster generates its GetMonsterData def, got {with_cat}"

    # a crawl-only monster (in AE's catalog, never captured in one of our maps)
    crawl_only = sorted(set(montemplates._crawled_catalog()) - set(montemplates.file_catalog()))
    mid = crawl_only[0]

    # GetMonsterData shape comes straight from the catalog column (1=1 with AE)
    cat = montemplates.catalog(conn, mid)
    assert cat["ID"] == mid and "Name" in cat, "catalog served from the DB (GetMonsterData shape)"

    # spawn path: a monBranch derived from the crawled def (renamed fields + an HP default)
    mb = montemplates.get(conn, mid)
    assert mb["MonID"] == mid and mb["intHPMax"] > 0, "crawl-only monster is spawnable from the DB"

    # a captured monster carries its rich monBranch in canonical columns
    cap_id = sorted(montemplates.file_catalog())[0]
    assert montemplates.get(conn, cap_id)["MonID"] == cap_id, "captured monBranch served from raw"

    # the store is live-editable: replace a monster's canonical record and get() reflects it immediately
    montemplates.store(conn, mid, {"MonID": mid, "strMonName": "Edited", "intHP": 7, "intHPMax": 7}, replace=True)
    conn.commit()
    assert montemplates.get(conn, mid)["strMonName"] == "Edited", "in-place edit served live"

    # unknown everywhere -> None
    assert montemplates.catalog(conn, 88888) is None and montemplates.get(conn, 88888) is None

    print(f"monsters OK: full catalog ({total}) DB-backed, GetMonsterData + spawn monBranch from "
          "the DB, crawl-only monsters spawnable, live-editable")
    print("ALL MONSTER TESTS PASSED")


if __name__ == "__main__":
    main()

