"""
Quest KB (data/questdb) — the compiled quest knowledge base the Beyond bot runs on.

Proves the objective->monster resolution mirrors record_kill's precedence (authored
quest_objective_refs first, then RefIDs, then name-match), that placements resolve to
real map/frame locations from the served monBranch, and that item turn-ins map to the
monsters whose drop tables carry the item (huntable=False when nothing does).
"""
import db
import seed
import questdb


def _obj(q, qoid):
    return next(o for o in q["objectives"] if o["qoid"] == qoid)


def main():
    db.init()
    seed.run()
    conn = db.connect()
    kb = questdb.build(conn)
    quests = kb["quests"]
    assert kb["version"] == 1 and len(quests) > 100, "KB compiled with the full quest table"
    print(f"KB OK: {len(quests)} quests, {len(kb['monsters'])} distinct target monsters")

    # Quest 19 (Lair): kill objective 158 has an AUTHORED refs row -> Water Draconian (206),
    # and the placement layer locates 206 in the lair. This is the chain the bot's bootstrap
    # chains.json hand-authored — the KB now derives it.
    q19 = quests["19"]
    o158 = _obj(q19, 158)
    assert o158["via"] == "authored" and o158["monsters"] == [206], "authored refs are authoritative"
    assert any(l["map"] == "lair" for l in o158["locations"]), "Water Draconian located in lair"
    assert q19["huntable"], "quest 19 is fully huntable"
    print(f"q19 OK: obj 158 -> mon 206 via authored @ "
          f"{[(l['map'], l['frame']) for l in o158['locations']]}")

    # Quest 20: the Wyvern objective (159) famously has NO RefIDs in the capture — record_kill
    # can't match it without the authored mapping. The KB must resolve it the same way.
    q20 = quests["20"]
    o159 = _obj(q20, 159)
    assert o159["via"] == "authored" and o159["monsters"] == [17], "RefID-less objective resolved"
    assert any(l["map"] == "lair" and l["frame"].lower() == "r2" for l in o159["locations"]), \
        "Wyvern located in lair/R2"
    print("q20 OK: RefID-less Wyvern objective resolved via authored refs to lair/R2")

    # Quest 1: kill objective 1 is authored (1,7,8); itemTurnin 14 wants item 54, which no
    # monster drops -> objective has no locations and the quest is NOT huntable end-to-end.
    q1 = quests["1"]
    o1 = _obj(q1, 1)
    assert o1["via"] == "authored" and set(o1["monsters"]) == {1, 7, 8}
    o14 = _obj(q1, 14)
    assert o14["type"] == 0 and o14["itemId"] == 54, "itemTurnin id read from ItemID/RefIDs"
    assert o14["sources"] == [] and not o14["globalDrop"] and not q1["huntable"], \
        "unsourced turn-in item marks the quest not huntable"
    print("q1 OK: kill objective authored; item 54 has no drop source -> huntable=False")

    # Quest 46: objective 169 -> monster 207, whose drop table carries item 87479 — so a
    # TURNIN objective for that item anywhere must list 207 as a source with its rate.
    mons, is_global = questdb._item_sources(conn, 87479)
    assert any(m["monId"] == 207 and m["rate"] == 0.5 for m in mons), \
        "monster_drops rows surface as item sources"
    print("item sources OK: item 87479 <- mon 207 @ rate 0.5")

    # Probabilistic objective drops (quest 42's gather objectives carry chance/min/max) ride
    # along so the bot can estimate kills-per-iteration.
    q42 = quests["42"]
    rolls = [o.get("drop") for o in q42["objectives"] if o.get("drop")]
    assert rolls and all(0 < r["chance"] <= 1 for r in rolls), "authored drop rolls surfaced"
    print(f"q42 OK: {len(rolls)} probabilistic objective drops surfaced")

    # Interact objectives surface the machine GameObject name(s) the bot clicks.
    # Quest 120 "Unlock the Keep" -> machine "FrontDoorOpen"; quest 59's armor
    # gather -> "DSPiece" (a prefix covering DSPiece1..6).
    o193 = _obj(quests["120"], 193)
    assert o193["type"] == 2 and o193["via"] == "interact" and o193["machines"] == ["FrontDoorOpen"], \
        "interact objective surfaces its machine name"
    o174 = _obj(quests["59"], 174)
    assert o174["machines"] == ["DSPiece"], "multi-piece interact surfaces the shared prefix"
    print("interact OK: q120 -> FrontDoorOpen, q59 -> DSPiece")

    # The webapi handler caches; a second call inside the TTL returns the same object.
    a = questdb.get(conn)
    b = questdb.get(conn)
    assert a is b, "TTL cache serves the compiled KB"
    assert questdb.get(conn, "fresh=1") is not b, "?fresh=1 forces a rebuild"
    print("cache OK: TTL hit + fresh=1 rebuild")

    conn.close()
    print("ALL OK")


if __name__ == "__main__":
    main()
