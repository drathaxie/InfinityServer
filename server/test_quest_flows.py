"""
Quest flows: repeatable re-accept + whole-word kill-credit name matching.

- A completed quest with Once=false can be re-accepted: status flips back to accepted, its
  objective counters reset, and its questsComplete bit clears. Once=true stays completed
  (mirrors the client's offer gate: !IsQuestComplete || IsRepeatable, IsRepeatable = !Once).
- record_kill's RefID-less fallback matches the monster name as a WHOLE WORD (plural-tolerant):
  killing a Rat no longer advances a 'Pirate Defeated' objective ('rat' is inside 'piRATe').
"""
import db
import seed
import game
import questdb


def _mk_quest(conn, qid, name, obj_name, once=False):
    """Author a minimal kill quest (one RefID-less killcount objective) via the same editor
    save path the DB manager uses."""
    game.quest_editor_save(conn, {
        "quest": {"QuestID": qid, "Name": name, "Desc": "", "EndText": "",
                  "Once": once, "NotificationType": 1},
        "turnins": [{"Name": obj_name, "QOType": game.QOT_KILL, "QOID": qid * 100,
                     "QuestID": qid, "RefIDs": "", "ItemID": -1, "Quantity": 1}],
        "drops": {}, "refs": {}, "rewards": []})


def main():
    db.use_throwaway()
    seed.run()
    conn = db.connect()
    char = game.login(conn, "__quester__", "pw")
    cid = char["id"]

    # --- whole-word kill-credit matching (unit) ---
    ok = game._name_in_objective
    assert not ok("rat", "Pirate Defeated"), "'rat' must NOT match inside 'piRATe'"
    assert ok("rat", "Rat Defeated"), "exact word matches"
    assert ok("sneevil", "Sneevils Defeated"), "plural 's' matches"
    assert ok("fox", "Foxes Slain"), "plural 'es' matches"
    assert ok("red dragon", "Red Dragon Slain"), "multi-word names match"
    assert not ok("dragon", "Dragonoid Defeated"), "no partial-word match"
    assert questdb._name_in_objective("rat", "Pirate Defeated") is False, \
        "questdb mirror agrees (bot hunts what the server credits)"

    # --- record_kill integration: the Rat/Pirate mis-credit is gone ---
    _mk_quest(conn, 9001, "Pirate Trouble", "Pirate Defeated")
    game.accept_quest(conn, char, 9001)
    qoid = 9001 * 100
    assert not game.record_kill(conn, char, 424242, "Rat"), \
        "killing a Rat must not credit 'Pirate Defeated'"
    assert game._obj_qty(conn, cid, qoid) == 0
    assert game.record_kill(conn, char, 424242, "Pirate"), "killing a Pirate credits it"
    assert game._obj_qty(conn, cid, qoid) == 1

    # --- repeatable quests ---
    # complete the (Once=false) quest, then re-accept: fresh run, bit cleared
    r = game.try_quest_complete(conn, char, 9001)
    assert r["Success"], r
    assert game._quest_status(conn, cid, 9001) == 2
    qd = game.quest_data(conn, char)
    assert qd["questsCopmlete"][9001 >> 3] & (1 << (9001 % 8)), "completion bit set"

    game.accept_quest(conn, char, 9001)
    assert game._quest_status(conn, cid, 9001) == 1, "repeatable re-accept flips to accepted"
    assert game._obj_qty(conn, cid, qoid) == 0, "objective counter reset for the fresh run"
    qd = game.quest_data(conn, char)
    assert not (qd["questsCopmlete"][9001 >> 3] & (1 << (9001 % 8))), \
        "completion bit clears on re-accept"
    assert 9001 in qd["questsAccepted"]
    # ...and it can be completed AGAIN (the whole point of a repeatable)
    assert game.record_kill(conn, char, 424242, "Pirate")
    assert game.try_quest_complete(conn, char, 9001)["Success"], "second run completes"

    # a Once=true quest stays completed: re-accept is a no-op ack
    _mk_quest(conn, 9002, "One Time Only", "Rat Defeated", once=True)
    game.accept_quest(conn, char, 9002)
    game.record_kill(conn, char, 424242, "Rat")
    assert game.try_quest_complete(conn, char, 9002)["Success"]
    game.accept_quest(conn, char, 9002)
    assert game._quest_status(conn, cid, 9002) == 2, "Once quest can't be re-accepted"

    print("quest flows OK: whole-word kill credit (Rat!=Pirate), repeatable re-accept + "
          "second completion, Once stays completed")
    print("ALL QUEST-FLOW TESTS PASSED")


if __name__ == "__main__":
    main()
