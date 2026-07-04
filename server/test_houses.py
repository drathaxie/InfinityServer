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

    print("houses OK: houseItems list + bag exclusion, equipHouse swap, deed->map mapping, "
          "per-frame save merge + '*' clear, houseData round-trip")
    print("ALL HOUSE TESTS PASSED")


if __name__ == "__main__":
    main()
