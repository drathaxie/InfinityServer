"""
Loot / drops — the kill-reward + Loot Inventory flow (capture 2026-06-18).

Proves the captured shapes: a kill's drops are held as PENDING loot carried by rewardPlayer
(items[] with LootIDs); getDrop keeps one (addItems with a real CharItemID + getLoot bSuccess);
bulkOperation IsLootAll keeps the rest (addItems + consumedLoot); discard clears without granting.
"""
import db
import seed
import game
import loot


def main():
    db.init()
    seed.run()
    conn = db.connect()
    conn.execute("DELETE FROM char_items WHERE char_id IN "
                 "(SELECT id FROM characters WHERE name='__loot__')")
    conn.execute("DELETE FROM characters WHERE name='__loot__'")
    conn.execute("DELETE FROM accounts WHERE username='__loot__'")
    conn.commit()
    char = game.login(conn, "__loot__", "pw")
    uid = game.uid_for(char)
    loot.clear(uid)
    start_inv = len(game.inventory(conn, char["id"]))

    # a kill rolls drops from the catalog and holds them as PENDING loot (rewardPlayer.items)
    items = [loot._catalog(conn, i) for i in loot.DROP_TABLE[:3]]
    wire = loot.add_pending(uid, items)
    assert len(wire) == 3 and all(w["LootID"] >= 2_700_000 for w in wire), "drops get unique LootIDs"
    assert all("ID" in w and w["Quantity"] >= 1 for w in wire), "drops carry catalog ID + Quantity"
    rp = loot.reward_packet(1553, gold_val=17, exp_val=170, exp_total=1104, items_wire=wire)
    assert rp["Cmd"] == "rewardPlayer" and rp["Gold"]["val"] == 17 and rp["Exp"]["val"] == 170
    assert rp["showDropWindow"] is True and rp["items"] == wire and rp["autoDiscarded"] == []
    assert len(loot.pending(uid)) == 3, "all three are pending until kept/discarded"
    print(f"reward OK: rewardPlayer carries {len(wire)} pending drops (LootIDs assigned)")

    # getDrop keeps ONE: it lands in the real inventory (addItems, CharItemID, LootID -1) and the
    # Loot Inventory drops it (getLoot bSuccess). Inventory grows by one; pending shrinks by one.
    first = wire[0]
    add, got = loot.take(conn, char["id"], uid, first["ID"], first["LootID"])
    assert got["Cmd"] == "getLoot" and got["bSuccess"] and got["LootID"] == first["LootID"]
    assert add["items"][0]["LootID"] == -1 and add["items"][0]["CharItemID"] > 0, "kept item is owned"
    assert add["items"][0]["ID"] == first["ID"]
    assert len(loot.pending(uid)) == 2, "one fewer pending after a keep"
    assert len(game.inventory(conn, char["id"])) == start_inv + 1, "kept item is in the inventory"

    # a getDrop for a LootID that isn't pending fails cleanly (bSuccess False, no addItems)
    miss_add, miss_got = loot.take(conn, char["id"], uid, first["ID"], 99)
    assert miss_add is None and miss_got["bSuccess"] is False, "unknown loot -> clean miss"
    print("getDrop OK: keep one -> addItems(owned)+getLoot; unknown loot -> bSuccess False")

    # bulkOperation IsLootAll keeps the REST: addItems for each + consumedLoot listing the LootIDs
    add_all, bulk = loot.take_all(conn, char["id"], uid)
    assert bulk["Cmd"] == "bulkOperation" and bulk["Success"] and bulk["IsLootAll"]
    assert len(add_all["items"]) == 2 and len(bulk["consumedLoot"]) == 2, "the two remaining are kept"
    assert {c["LootID"] for c in bulk["consumedLoot"]} == {w["LootID"] for w in wire[1:]}
    assert loot.pending(uid) == [], "nothing pending after loot-all"
    assert len(game.inventory(conn, char["id"])) == start_inv + 3, "all three drops now owned"
    print(f"bulkOperation OK: loot-all kept {len(add_all['items'])}, consumedLoot listed, window cleared")

    # discard clears pending WITHOUT granting (Discard All)
    loot.add_pending(uid, items)
    inv_before = len(game.inventory(conn, char["id"]))
    loot.discard_all(uid)
    assert loot.pending(uid) == [] and len(game.inventory(conn, char["id"])) == inv_before, \
        "discard drops pending loot without adding it to the inventory"
    print("discard OK: Discard All clears pending without granting")
    print("\nALL LOOT TESTS PASSED")


if __name__ == "__main__":
    main()
