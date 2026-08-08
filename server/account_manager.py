"""Player account management and inventory-token item exchanges."""
import os
import re
import time

import db
import game
import friends
import guilds


USERNAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9 _-]{2,23}$")
# Production currently has Golden Item of Digital Awesomeness (50727). Add future
# Epic/Wicked/Mythic variants as a comma-separated list without a code deploy.
TOKEN_ITEM_IDS = tuple(dict.fromkeys(int(x.strip()) for x in
    os.environ.get("INFINITY_REDEMPTION_TOKEN_ITEM_IDS",
                   os.environ.get("INFINITY_REDEMPTION_TOKEN_ITEM_ID", "50727")).split(",")
    if x.strip().isdigit())) or (50727,)
TOKEN_ITEM_ID = TOKEN_ITEM_IDS[0]


def account_for_session(conn, account_id):
    return conn.execute(
        "SELECT a.id,a.username,a.created,a.last_accessed,c.id AS char_id,c.name,c.level,c.gold,c.coins,"
        "c.upgrade_days,c.upgrade_expires,c.guild_id,c.guild_rank "
        "FROM accounts a JOIN characters c ON c.account_id=a.id WHERE a.id=?",
        (int(account_id),)).fetchone()


def change_username(conn, account_id, current_password, new_username):
    new_username = (new_username or "").strip()
    if not USERNAME_RE.fullmatch(new_username):
        return False, "Use 3-24 characters; start with a letter and use letters, numbers, spaces, _ or -."
    acc = conn.execute("SELECT * FROM accounts WHERE id=?", (int(account_id),)).fetchone()
    if acc is None or not game.verify_password(current_password or "", acc["password"] or "")[0]:
        return False, "Current password is incorrect."
    taken = conn.execute("SELECT id FROM accounts WHERE LOWER(username)=LOWER(?) AND id<>?",
                         (new_username, int(account_id))).fetchone()
    if taken:
        return False, "That username is already taken."
    conn.execute("UPDATE accounts SET username=?,session_token=NULL WHERE id=?",
                 (new_username, int(account_id)))
    conn.execute("UPDATE characters SET name=? WHERE account_id=?",
                 (new_username, int(account_id)))
    conn.commit()
    return True, "Username changed. Sign in to the game again."


def change_password(conn, account_id, current_password, new_password):
    if len(new_password or "") < 8 or len(new_password or "") > 128:
        return False, "New password must be 8-128 characters."
    acc = conn.execute("SELECT password FROM accounts WHERE id=?", (int(account_id),)).fetchone()
    if acc is None or not game.verify_password(current_password or "", acc["password"] or "")[0]:
        return False, "Current password is incorrect."
    conn.execute("UPDATE accounts SET password=?,session_token=NULL WHERE id=?",
                 (game.hash_password(new_password), int(account_id)))
    conn.commit()
    return True, "Password changed. Sign in to the game again."


def catalog(conn, query="", limit=5000):
    # The prize table exposes the complete catalog. Retain a high safety ceiling
    # so a malformed request cannot create an unbounded response.
    limit = max(1, min(int(limit), 10000))
    q = (query or "").strip()
    if q.isdigit():
        rows = conn.execute(
            "SELECT i.item_id,i.name,i.item_type,i.equip_spot,i.rarity,i.cost,i.coins,i.upgrade_only,"
            "(SELECT COUNT(*) FROM token_redemptions tr WHERE tr.item_id=i.item_id) AS popularity "
            "FROM items i WHERE i.item_id=? OR LOWER(i.name) LIKE LOWER(?) "
            "ORDER BY popularity DESC,i.name,i.item_id LIMIT ?",
            (int(q), f"%{q}%", limit)).fetchall()
    else:
        rows = conn.execute(
            "SELECT i.item_id,i.name,i.item_type,i.equip_spot,i.rarity,i.cost,i.coins,i.upgrade_only,"
            "(SELECT COUNT(*) FROM token_redemptions tr WHERE tr.item_id=i.item_id) AS popularity "
            "FROM items i WHERE LOWER(i.name) LIKE LOWER(?) "
            "ORDER BY popularity DESC,i.name,i.item_id LIMIT ?",
            (f"%{q}%", limit)).fetchall()
    return [{"id": int(r["item_id"]), "name": r["name"] or f"Item {r['item_id']}",
             "type": int(r["item_type"] or 0), "equipSpot": int(r["equip_spot"] or 0),
             "rarity": int(r["rarity"] or 0), "price": int(r["cost"] or 0),
             "ac": bool(r["coins"]), "member": bool(r["upgrade_only"]),
             "kbOnly": False, "popularity": int(r["popularity"] or 0)} for r in rows]


def token_balance(conn, char_id):
    marks = ",".join("?" for _ in TOKEN_ITEM_IDS)
    row = conn.execute(
        "SELECT COALESCE(SUM(quantity),0) AS n FROM char_items "
        f"WHERE char_id=? AND item_id IN ({marks}) AND banked=0 AND equipped=0",
        (int(char_id), *TOKEN_ITEM_IDS)).fetchone()
    return int(row["n"] or 0)


def token_inventory(conn, char_id):
    marks = ",".join("?" for _ in TOKEN_ITEM_IDS)
    rows = conn.execute(
        "SELECT ci.item_id,i.name,COALESCE(SUM(ci.quantity),0) AS n FROM char_items ci "
        "JOIN items i ON i.item_id=ci.item_id WHERE ci.char_id=? "
        f"AND ci.item_id IN ({marks}) AND ci.banked=0 AND ci.equipped=0 "
        "GROUP BY ci.item_id,i.name ORDER BY ci.item_id",
        (int(char_id), *TOKEN_ITEM_IDS)).fetchall()
    return [{"itemId": int(r["item_id"]), "name": r["name"] or "IoDA Token",
             "count": int(r["n"] or 0)} for r in rows]


def inventory(conn, char_id):
    rows = conn.execute(
        "SELECT ci.item_id,i.name,i.item_type,i.equip_spot,i.rarity,"
        "SUM(ci.quantity) AS quantity,MAX(ci.equipped) AS equipped,MAX(ci.banked) AS banked "
        "FROM char_items ci JOIN items i ON i.item_id=ci.item_id WHERE ci.char_id=? "
        "GROUP BY ci.item_id,i.name,i.item_type,i.equip_spot,i.rarity ORDER BY i.name,ci.item_id",
        (int(char_id),)).fetchall()
    return [{"itemId": int(r["item_id"]), "name": r["name"] or f"Item {r['item_id']}",
             "type": int(r["item_type"] or 0), "equipSpot": int(r["equip_spot"] or 0),
             "rarity": int(r["rarity"] or 0), "count": int(r["quantity"] or 0),
             "equipped": bool(r["equipped"]), "banked": bool(r["banked"])} for r in rows]


def buyback_history(conn, account_id):
    rows = conn.execute(
        "SELECT b.id,b.item_id,i.name,i.item_type,b.remaining_quantity,b.unit_price,b.sold_at "
        "FROM ac_item_buybacks b JOIN items i ON i.item_id=b.item_id "
        "WHERE b.account_id=? AND b.remaining_quantity>0 ORDER BY b.sold_at DESC,b.id DESC",
        (int(account_id),)).fetchall()
    return [{"id": int(r["id"]), "itemId": int(r["item_id"]),
             "name": r["name"] or f"Item {r['item_id']}", "type": int(r["item_type"] or 0),
             "count": int(r["remaining_quantity"]), "price": int(r["unit_price"]),
             "soldAt": float(r["sold_at"])} for r in rows]


def buy_back(conn, account_id, buyback_id, quantity=1):
    account = account_for_session(conn, account_id)
    try:
        quantity, buyback_id = max(1, int(quantity)), int(buyback_id)
    except (TypeError, ValueError):
        return False, "Invalid buyback request.", None
    row = conn.execute(
        "SELECT * FROM ac_item_buybacks WHERE id=? AND account_id=? AND remaining_quantity>0",
        (buyback_id, int(account_id))).fetchone()
    if account is None or row is None:
        return False, "That item is no longer available for buyback.", None
    quantity = min(quantity, int(row["remaining_quantity"]))
    cost = int(row["unit_price"]) * quantity
    if int(account["coins"]) < cost:
        return False, "You do not have enough AdventureCoins.", None
    item = db.item(conn, int(row["item_id"]))
    if item is None:
        return False, "That item is no longer in the catalog.", None
    try:
        conn.execute("UPDATE characters SET coins=coins-? WHERE id=?", (cost, account["char_id"]))
        for _ in range(quantity):
            game._grant_item(conn, int(account["char_id"]), item)
        conn.execute("UPDATE ac_item_buybacks SET remaining_quantity=remaining_quantity-? WHERE id=?",
                     (quantity, buyback_id))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return True, f"{item.get('Name') or 'Item'} was returned to your inventory.", cost


def friend_manager(conn, char_id):
    return [{"id": int(x["ID"]), "name": x["Name"], "level": int(x["Level"]),
             "server": x["Server"]} for x in friends.friend_list(conn, int(char_id))]


def remove_friend(conn, char_id, friend_id):
    try:
        friend_id = int(friend_id)
    except (TypeError, ValueError):
        return False, "Invalid friend."
    if not friends.are_friends(conn, int(char_id), friend_id):
        return False, "That player is not on your friends list."
    friends.unlink(conn, int(char_id), friend_id)
    conn.commit()
    return True, "Friend removed."


def guild_manager(conn, account):
    gid = int(account["guild_id"] or 0)
    obj = guilds.guild_object(conn, gid)
    if not obj:
        return None
    return {"id": int(obj["ID"]), "name": obj["Name"], "motd": obj["MOTD"],
            "rank": int(account["guild_rank"] or 0), "maxMembers": int(obj["maxMembers"]),
            "members": [{"id": int(x["ID"]), "name": x["userName"],
                         "level": int(x["Level"]), "rank": int(x["Rank"]),
                         "server": x["Server"]} for x in obj["Users"].values()]}


def set_guild_motd(conn, account, motd):
    if not account["guild_id"] or int(account["guild_rank"] or 0) < guilds.RANK_OFFICER:
        return False, "Only guild officers can change the message."
    motd = (motd or "").strip()
    if len(motd) > 250:
        return False, "Guild message must be 250 characters or fewer."
    conn.execute("UPDATE guilds SET motd=? WHERE id=?", (motd, account["guild_id"]))
    conn.commit()
    return True, "Guild message updated."


def leave_guild(conn, account):
    char = conn.execute("SELECT * FROM characters WHERE id=?", (account["char_id"],)).fetchone()
    if not char or not char["guild_id"]:
        return False, "You are not in a guild."
    guilds.leave(conn, char)
    return True, "You left the guild."


def house_manager(conn, char_id):
    homes, furniture = [], []
    for item in game.house_items(conn, int(char_id)):
        row = {"itemId": int(item["ItemID"]), "charItemId": int(item["CharItemID"]),
               "name": item["sName"], "count": int(item["iQty"]),
               "equipped": bool(item["bEquip"]), "type": item["sType"]}
        (homes if item["sType"] == "House" else furniture).append(row)
    return {"houses": homes, "furniture": furniture}


def equip_house(conn, account, item_id):
    char = conn.execute("SELECT * FROM characters WHERE id=?", (account["char_id"],)).fetchone()
    packet = game.equip_house(conn, char, item_id) if char else None
    return (True, packet["Msg"]) if packet else (False, "You do not own that house.")


def redemption_history(conn, account_id, limit=50):
    rows = conn.execute(
        "SELECT tr.item_id,i.name,i.item_type,tr.token_item_id,ti.name AS token_name,tr.redeemed "
        "FROM token_redemptions tr JOIN items i ON i.item_id=tr.item_id "
        "LEFT JOIN items ti ON ti.item_id=tr.token_item_id WHERE tr.account_id=? "
        "ORDER BY tr.redeemed DESC LIMIT ?", (int(account_id), int(limit))).fetchall()
    return [{"itemId": int(r["item_id"]), "name": r["name"] or f"Item {r['item_id']}",
             "type": int(r["item_type"] or 0), "tokenItemId": int(r["token_item_id"]),
             "token": r["token_name"] or "IoDA Token", "redeemed": float(r["redeemed"])}
            for r in rows]


def redeem(conn, account_id, item_id):
    """Atomically consume one inventory token and grant one selected catalog item."""
    account = account_for_session(conn, account_id)
    item = db.item(conn, int(item_id)) if str(item_id).isdigit() else None
    if account is None or item is None:
        return False, "That item is not available.", None
    if int(item_id) in TOKEN_ITEM_IDS:
        return False, "A token cannot be exchanged for itself.", None
    try:
        marks = ",".join("?" for _ in TOKEN_ITEM_IDS)
        token = conn.execute(
            "SELECT char_item_id,item_id,quantity FROM char_items WHERE char_id=? "
            f"AND item_id IN ({marks}) AND banked=0 AND equipped=0 AND quantity>0 "
            "ORDER BY char_item_id LIMIT 1",
            (int(account["char_id"]), *TOKEN_ITEM_IDS)).fetchone()
        if token is None:
            return False, "You do not have a redemption token in your inventory.", None
        if int(token["quantity"]) == 1:
            changed = conn.execute("DELETE FROM char_items WHERE char_item_id=? AND quantity=1",
                                   (token["char_item_id"],)).rowcount
        else:
            changed = conn.execute(
                "UPDATE char_items SET quantity=quantity-1 WHERE char_item_id=? AND quantity>1",
                (token["char_item_id"],)).rowcount
        if changed != 1:
            conn.rollback()
            return False, "Your token balance changed; please try again.", None
        game._grant_item(conn, int(account["char_id"]), item)
        conn.execute(
            "INSERT INTO token_redemptions(account_id,char_id,token_item_id,item_id,redeemed) "
            "VALUES(?,?,?,?,?)", (int(account_id), int(account["char_id"]), int(token["item_id"]),
                                  int(item_id), time.time()))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return True, f"{item.get('Name') or 'Item'} was added to your account.", item
