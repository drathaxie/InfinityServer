"""Compatibility coverage for the 2026-07-06 client contract."""
import asyncio
import json
from types import SimpleNamespace

import combat
import db
import forge
import game
import seed
import statues
from handlers import players as player_handlers


def main():
    db.use_throwaway()
    seed.run()
    conn = db.connect()
    char = game.login(conn, "__client_delta__", "pw")

    achievements = {"ip25": 1 << 6}

    made, house_item = statues.generate(conn, char, now=1000)
    conn.commit()
    assert made["Success"] and made["ItemID"] == statues.STATUE_ITEM_ID
    assert house_item["sType"] == "FloorItem" and house_item["Meta"].startswith(
        f"custom:1,cid:{char['id']},rev:")
    assert house_item["MobileCompatibility"] == 1
    custom = db.item(conn, statues.STATUE_ITEM_ID)
    assert custom["Name"] == "Custom Hero Statue"
    assert custom.get("Bundle") is None
    day_one = db.item(conn, 99514)
    if day_one is not None:
        assert day_one["Name"] == "Day 1 Backer of DOOOOM Statue"
        assert day_one["ID"] != statues.STATUE_ITEM_ID
    custom_house_items = [x for x in game.house_items(conn, char["id"])
                          if x["ItemID"] == statues.STATUE_ITEM_ID]
    assert len(custom_house_items) == 1
    assert custom_house_items[0]["MobileCompatibility"] == 1

    png = statues.render_png(conn, char["id"])
    assert png is None, "the server must not invent substitute character art"
    render = (statues.PNG_SIGNATURE + b"\x00\x00\x00\x0dIHDR"
              + (1024).to_bytes(4, "big") + (1280).to_bytes(4, "big")
              + b"\x08\x06\x00\x00\x00" + b"\x00" * 48)
    assert statues.store_render(conn, char["id"], render)
    conn.commit()
    assert statues.render_png(conn, char["id"]) == render

    # The dedicated response must resolve StatueGenerator's pending modal before
    # the optional buyItem inventory refresh is dispatched.
    wire_char = game.login(conn, "__client_delta_wire__", "pw")

    packets = []

    class Writer:
        def write(self, payload):
            packets.append(json.loads(payload.rstrip(b"\x00").decode("utf-8")))

        async def drain(self):
            return None

    session = SimpleNamespace(conn=conn, char=wire_char, member=object())
    asyncio.run(player_handlers.generate_statue_live(
        session, Writer(), "generateStatue", [], {}))
    assert [p["Cmd"] for p in packets] == ["generateStatue", "buyItem"]
    assert packets[0]["Success"] is True
    assert packets[1]["houseItem"]["ItemID"] == statues.STATUE_ITEM_ID

    cooldown, duplicate = statues.generate(conn, char, now=1001)
    assert not cooldown["Success"] and cooldown["CooldownRemainingMs"] > 0
    assert duplicate is None

    dev_char = game.login(conn, "__client_delta_dev__", "pw")
    conn.execute("UPDATE characters SET access_level=40 WHERE id=?",
                 (dev_char["id"],))
    dev_char = conn.execute(
        "SELECT * FROM characters WHERE id=?", (dev_char["id"],)).fetchone()
    dev_first, dev_item = statues.generate(conn, dev_char, now=2000)
    dev_second, dev_refresh_item = statues.generate(conn, dev_char, now=2001)
    assert dev_first["Success"] and dev_first["CooldownRemainingMs"] == 0
    assert dev_second["Success"] and dev_second["CooldownRemainingMs"] == 0, \
        "dev accounts bypass the statue cooldown"
    assert dev_item is not None and dev_refresh_item is None

    refreshed, refresh_item = statues.generate(conn, char, now=1301)
    conn.commit()
    assert refreshed["Success"] and refresh_item is None
    count = conn.execute(
        "SELECT COUNT(*) AS n FROM char_items WHERE char_id=? AND item_id=?",
        (char["id"], statues.STATUE_ITEM_ID)).fetchone()["n"]
    assert count == 1, "regeneration updates the decoratable item instead of stacking it"

    # An invalid HairID can no longer produce changeColor.hairBundle=null.
    conn.execute("UPDATE characters SET hair_id=999999 WHERE id=?", (char["id"],))
    char = conn.execute("SELECT * FROM characters WHERE id=?", (char["id"],)).fetchone()
    applied = game.save_customization(conn, char, [1, 2, 3, 4, 5, 6, 999999])
    assert applied["hair_id"] != 999999
    assert game._hair_info(conn, applied["hair_id"], char["gender"]) is not None

    # Newly authored graph fields survive server rendering.
    particle, _, _ = combat._render_node(
        "test", 1, "p:1", [], {}, {"Name": "Particle", "Lifetime": 875})
    assert particle["Lifetime"] == 875
    spell, _, _ = combat._render_node(
        "test", 1, "p:1", ["m:7"], {},
        {"Name": "SpellAnimation", "FX": "METEOR", "SpellGraphic": "Meteor",
         "X": 1.5, "Y": -2, "Ease": "easeoutquad", "ProjSpeed": 9})
    assert spell["FX"] == "METEOR" and spell["target"] == "m:7"
    cancel, _, _ = combat._render_node(
        "test", 1, "p:1", [], {}, {"Name": "AnimationCancel"})
    assert cancel == {"Name": "AnimationCancel"}

    skill = conn.execute("SELECT skill_id FROM skills ORDER BY skill_id LIMIT 1").fetchone()
    conn.execute("UPDATE skills SET auto_hold_at_range=1 WHERE skill_id=?",
                 (skill["skill_id"],))
    row = conn.execute("SELECT * FROM skills WHERE skill_id=?", (skill["skill_id"],)).fetchone()
    assert forge.skill_object(row)["AutoHoldAtRange"] is True
    conn.close()
    print("client-delta statue/protocol compatibility: OK")


if __name__ == "__main__":
    main()

