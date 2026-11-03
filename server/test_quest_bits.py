"""Read quest completion exactly as the shipped PlayerQuestData client does."""
import json
import sqlite3
from pathlib import Path

import db
import game


def client_complete(packet, qid, field="questsCopmlete"):
    bits = packet[field]
    index = len(bits) - 1 - (qid >> 3)
    return qid >= 0 and index >= 0 and bool(bits[index] & (1 << (qid % 8)))


def test_completion_wire_bits():
    previous_backend = db.BACKEND
    db.BACKEND = "sqlite"
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    try:
        db.initialize_connection(conn)
        aid = conn.execute("INSERT INTO accounts(username,password,created) VALUES(?,?,?) RETURNING id",
                           ("quest-wire-test", "unused", 0)).fetchone()[0]
        cid = conn.execute("INSERT INTO characters(account_id,name) VALUES(?,?) RETURNING id",
                           (aid, "Quest Wire Test")).fetchone()[0]
        char = conn.execute("SELECT * FROM characters WHERE id=?", (cid,)).fetchone()
        source = json.loads((Path(__file__).resolve().parents[1] / "data/quests.json").read_text(encoding="utf-8"))
        if isinstance(source, dict):
            source = source.values()
        quest = next(q for q in source if q.get("QuestID") == 301)
        assert quest["turnin"][0]["DialogID"] == 95
        game.quest_editor_save(conn, {"quest": quest, "turnins": quest["turnin"],
                                      "drops": {}, "refs": {}, "rewards": []})

        # Boarding must actually be credited before turn-in; unrelated scenes don't count.
        game.watch_cutscene(conn, char, 95)
        assert game._obj_qty(conn, cid, 670) == 0, "not accepted: no boarding credit"
        game.accept_quest(conn, char, 301)
        assert not game.try_quest_complete(conn, char, 301)["Success"]
        game.watch_cutscene(conn, char, 94)
        assert game._obj_qty(conn, cid, 670) == 0
        game.watch_cutscene(conn, char, 95)
        assert game._obj_qty(conn, cid, 670) == 1
        assert game.try_quest_complete(conn, char, 301)["Success"]
        assert client_complete(game.quest_data(conn, char), 301), "client must see completed boarding quest"
        game.accept_quest(conn, char, 301)
        qd = game.quest_data(conn, char)
        assert 301 not in qd["questsAccepted"] and client_complete(qd, 301), "Once quest stays completed"

        # Adjacent and widely separated IDs catch byte order, boundaries, and aliasing.
        completed = {0, 7, 8, 255, 256, 301, 9001, game.QUESTS_COMPLETE_BYTES * 8 - 1}
        for qid in completed - {301}:
            conn.execute("INSERT INTO char_quests(char_id,quest_id,status) VALUES(?,?,2)", (cid, qid))
        conn.commit()
        qd = game.quest_data(conn, char)
        assert all(client_complete(qd, qid) == (qid in completed)
                   for qid in range(game.QUESTS_COMPLETE_BYTES * 8))

        # Reset only Asteroid: other quest flags survive in the same client wire order.
        reset = game.reset_saga(conn, char, 16)
        assert not client_complete(reset, 301, "qComplete")
        assert all(client_complete(reset, qid, "qComplete") for qid in completed - {301})
        assert reset["qComplete"] == game.quest_data(conn, char)["questsCopmlete"]
        game.accept_quest(conn, char, 301)
        assert not game.try_quest_complete(conn, char, 301)["Success"], "story reset clears boarding credit"
        game.watch_cutscene(conn, char, 95)
        assert game.try_quest_complete(conn, char, 301)["Success"]
        assert client_complete(game.quest_data(conn, char), 301)
    finally:
        conn.close()
        db.BACKEND = previous_backend


if __name__ == "__main__":
    test_completion_wire_bits()
    print("QUEST WIRE BITS PASSED: boarding, all 16000 flag positions, one-time completion, story reset/replay")
