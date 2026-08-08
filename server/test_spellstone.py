"""Practice Spellstone: catalog -> MetaString Skill Forge graph -> Frogzard morph -> expiry."""
import asyncio
import json
import time

import combat
import db
import forge
import seed
import world
from handlers.items import use_spellstone
from handlers.combat_cmds import begin_cast as cast_slot


class Writer:
    def __init__(self):
        self.data = b""

    def write(self, data):
        self.data += data

    async def drain(self):
        pass


class Member:
    uid = 777
    name = "Spellstone Tester"


class Session:
    pass


def run():
    db.use_throwaway()
    db.init()
    with db.connect() as conn:
        seed.seed_practice_spellstone(conn)
        conn.execute("INSERT INTO accounts(id,username,password) VALUES(1,'stone','x')")
        conn.execute("INSERT INTO characters(id,account_id,name) VALUES(1,1,'Stone Tester')")
        conn.execute("INSERT INTO char_items(char_item_id,char_id,item_id,quantity) VALUES(1,1,?,1)",
                     (seed.PRACTICE_SPELLSTONE_ITEM_ID,))
        conn.commit()

        item = db.item(conn, seed.PRACTICE_SPELLSTONE_ITEM_ID)
        assert item["ItemType"] == 44 and item["MetaString"] == str(seed.PRACTICE_SPELLSTONE_SKILL_ID)
        skill = forge.skill_by_id(conn, item["MetaString"])
        attack, killed, damage = combat.cast_skill("battleon", 777, -1, None,
                                                    skill["data"], skill["forge"],
                                                    skill["skill_id"], ["p:777"])
        morph = next(n for n in attack["Nodes"] if n["Name"] == "MonTransform")
        assert morph["Bundle"]["ID"] == 46555 and morph["Linkage"] == "Frogzard"
        assert not killed and damage == 0

        session = Session()
        session.conn, session.char = conn, conn.execute("SELECT * FROM characters WHERE id=1").fetchone()
        session.member, session.area, session.equipped_class = Member(), "battleon", 1932
        writer = Writer()
        asyncio.run(use_spellstone(session, writer, "useSpellstone",
                                   [str(seed.PRACTICE_SPELLSTONE_ITEM_ID), ""], {}))
        packets = [json.loads(p) for p in writer.data.rstrip(b"\0").split(b"\0")]
        assert [p["Cmd"] for p in packets] == ["equipItem", "sEAct"]
        assert packets[1]["skillList"]["5"]["id"] == seed.PRACTICE_SPELLSTONE_SKILL_ID
        assert forge.equipped_spellstone(conn, 1)["item_id"] == seed.PRACTICE_SPELLSTONE_ITEM_ID

        writer = Writer()
        asyncio.run(cast_slot(session, writer, "gar", ["5", ""], {}))
        cast_packets = [json.loads(p) for p in writer.data.rstrip(b"\0").split(b"\0")]
        assert len(cast_packets) == 1
        assert any(n["Name"] == "MonTransform" for n in cast_packets[0]["Nodes"])

        aura = combat._auras[("battleon", "p:777")]["Practice Frogzard Form"]
        aura["ends"] = time.time() - 1
        expired = combat.aura_ticks()
        assert any(any(n.get("detransform") for n in p["Nodes"])
                   for _area, p, _kills in expired if p.get("Cmd") == "Attack")
    print("spellstone OK: equip -> slot 5 -> MetaString Skill Forge graph -> Frogzard -> timed revert")


if __name__ == "__main__":
    run()
