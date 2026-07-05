"""
Houses v1: deeds + furniture live in initPlayer.houseItems (not the bag), a deed equips via
the equipHouse flow, each deed resolves to its house map (cottage default; the minted
Kickstarter Flying Castle deed opens housekickstarterflyingcastle; kv-overridable), housesave
merges per-frame placement saves into the stored {frame:[...]} dict, and build_house_data
serves it back as AreaJoin houseData.
"""
import json

import db
import seed
import game

DEED = 5291          # Hollowsoul Castle (equip_spot 8) -> default map (the cottage)
DEED_KS = 200001     # Kickstarter Flying Castle (OUR minted deed) -> housekickstarterflyingcastle
FURNITURE = 1407     # Pactagonal Knight Statue (equip_spot 9, FloorItem)


def main():
    db.use_throwaway()
    seed.run()
    conn = db.connect()
    char = game.login(conn, "__homeowner__", "pw")
    cid = char["id"]

    # grant a deed + furniture: they show in houseItems (houseItem shape), NOT the bag
    game.give_item(conn, char, DEED, 1)
    game.give_item(conn, char, FURNITURE, 2)
    hi = {h["ItemID"]: h for h in game.house_items(conn, cid)}
    assert set(hi) == {DEED, FURNITURE}, hi.keys()
    assert hi[DEED]["sType"] == "House" and hi[FURNITURE]["sType"] == "FloorItem"
    assert hi[DEED]["MobileCompatibility"] == 1, "must be 1 or the client refuses entry"
    assert hi[DEED]["bEquip"] == 0 and hi[FURNITURE]["iQty"] == 2
    bag_ids = {i["ID"] for i in game.inventory(conn, cid)}
    assert DEED not in bag_ids and FURNITURE not in bag_ids, \
        "houses/furniture are not regular inventory"
    init = game.build_init_player(conn, char)
    assert {h["ItemID"] for h in init["houseItems"]} == {DEED, FURNITURE}
    assert init["playerInfo"]["EquippedHouseItemID"] == -1, "no house equipped yet"

    # equip the deed: equipHouse reply, bEquip flips, EquippedHouseItemID set; a second deed
    # swaps (one equipped house at a time); the avatar equip path never touches houses
    assert game.equip_item(conn, char, DEED) is None, "deed must not equip via the avatar rig"
    resp = game.equip_house(conn, char, DEED)
    assert resp and resp["Cmd"] == "equipHouse" and resp["ItemID"] == DEED, resp
    assert game.equipped_house_id(conn, cid) == DEED
    game.give_item(conn, char, DEED_KS, 1)
    assert game.equip_house(conn, char, DEED_KS)["ItemID"] == DEED_KS
    assert game.equipped_house_id(conn, cid) == DEED_KS, "equipping a deed swaps the old one"
    assert game.equip_house(conn, char, FURNITURE) is None, "furniture isn't equippable"
    assert game.equip_house(conn, char, 8736) is None, "unowned deed refuses"

    # deed -> map: cottage default, the minted KS deed -> flying castle, kv override wins
    assert game.house_map_for(conn, DEED) == "house", "unmapped deeds open the cottage"
    assert game.house_map_for(conn, DEED_KS) == "housekickstarterflyingcastle"
    db.kv_set(conn, "house_maps", json.dumps({str(DEED): "clubhouse"}))
    assert game.house_map_for(conn, DEED) == "clubhouse", "kv override wins"
    db.kv_set(conn, "house_maps", "{}")

    # housesave merges PER-FRAME saves into one layout dict; '*' clears the whole house
    place = lambda n: json.dumps([{"ItemID": FURNITURE, "x": float(n), "y": 0.0,
                                   "scaleX": 1.0, "scaleY": 1.0, "layerName": "BGFront"}])
    assert game.house_save(conn, char, DEED_KS, "Enter", place(1))["success"]
    assert game.house_save(conn, char, DEED_KS, "Bedroom", place(2))["success"]
    layout = game._house_layout(conn, cid, DEED_KS)
    assert set(layout) == {"Enter", "Bedroom"}, "per-frame saves merge, not overwrite"
    assert layout["Enter"][0]["x"] == 1.0 and layout["Bedroom"][0]["x"] == 2.0
    assert game.house_save(conn, char, DEED_KS, "Bedroom", "[]")["success"]
    assert set(game._house_layout(conn, cid, DEED_KS)) == {"Enter"}, "emptied room drops out"
    assert not game.house_save(conn, char, DEED_KS, "Enter", "{bad json")["success"]
    assert game.house_save(conn, char, DEED_KS, "*", "[]")["success"]
    assert game._house_layout(conn, cid, DEED_KS) == {}, "'*' clears the whole house"

    # build_house_data: the AreaJoin houseData — saved placements round-trip, owner lowercase
    game.house_save(conn, char, DEED_KS, "Enter", place(7))
    hd = game.build_house_data(conn, char)
    assert hd["unm"] == "__homeowner__", "unm is the lowercase owner name (ownership check)"
    assert {h["ItemID"] for h in hd["items"]} == {DEED, FURNITURE, DEED_KS}
    got = json.loads(hd["sHouseInfo"])
    assert got["Enter"][0]["x"] == 7.0, "saved placements ride back in sHouseInfo"

    # --- buying a house: the reply carries houseItem (never the bag `item`), and a player's
    # FIRST deed auto-equips; a second one never steals the equipped home
    buyer = game.login(conn, "__firsthome__", "pw")
    conn.execute("UPDATE characters SET gold=200000 WHERE id=?", (buyer["id"],))
    conn.commit()
    buyer = conn.execute("SELECT * FROM characters WHERE id=?", (buyer["id"],)).fetchone()
    resp = game.buy(conn, buyer, ["0", "2688", "200001"])   # the Backer Shop's castle deed
    assert resp["Success"] and resp.get("houseItem"), resp
    assert resp["houseItem"]["ItemID"] == 200001 and "item" not in resp, \
        "house purchases reply with houseItem, never a bag item"
    eq = game.auto_equip_first_house(conn, buyer, 200001)
    assert eq is not None and eq["Cmd"] == "equipHouse", "first deed auto-equips"
    assert game.equipped_house_id(conn, buyer["id"]) == 200001
    game.give_item(conn, buyer, DEED, 1)
    assert game.auto_equip_first_house(conn, buyer, DEED) is None, \
        "a second deed must NOT steal the equipped home"
    assert game.equipped_house_id(conn, buyer["id"]) == 200001, "home unchanged"
    assert game.auto_equip_first_house(conn, buyer, FURNITURE) is None, "furniture never equips"

    # --- offline visiting: /house <name> serves ANY player's house from the DB — the owner
    # (__homeowner__, equipped castle + saved layout above) is never connected here
    _visit_offline_house(conn)

    print("houses OK: houseItems list + bag exclusion, equipHouse swap, deed->map mapping, "
          "per-frame save merge + '*' clear, houseData round-trip, first-house auto-equip, "
          "offline visit")
    print("ALL HOUSE TESTS PASSED")


def _visit_offline_house(conn):
    import asyncio

    import server
    import world

    class FakeWriter:
        def __init__(self):
            self.data = bytearray()
            self.closed = False

        def write(self, b):
            self.data.extend(b)

        async def drain(self):
            pass

        def close(self):
            self.closed = True

    visitor = game.login(conn, "__tourist__", "pw")

    async def run():
        w = FakeWriter()
        s = server.Session(w)
        s.char = s.conn.execute("SELECT * FROM characters WHERE id=?",
                                (visitor["id"],)).fetchone()
        s.member = world.Member(game.uid_for(visitor), visitor["name"], {}, w)
        s.area = "battleon-1"
        world.join(s.member, s.area)
        await server.dispatch(s, w, json.dumps(
            {"Cmd": "house", "Params": ["__HomeOwner__"]}).encode())   # case-insensitive
        pkts = [json.loads(p) for p in bytes(w.data).split(b"\x00") if p]
        join = next((p for p in pkts if p.get("houseData") is not None), None)
        assert join is not None, f"no houseData AreaJoin served: {[p.get('Cmd') for p in pkts]}"
        assert join["houseData"]["unm"] == "__homeowner__", "owner rides in houseData.unm"
        assert json.loads(join["houseData"]["sHouseInfo"])["Enter"][0]["x"] == 7.0, \
            "the OFFLINE owner's saved layout is served"
        assert join["areaName"].startswith("housekickstarterflyingcastle-"), join["areaName"]
        s.close()

    asyncio.run(run())


if __name__ == "__main__":
    main()
