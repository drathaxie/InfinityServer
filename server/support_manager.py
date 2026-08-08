"""Anonymous-by-design staff support and account activity helpers."""
import json
import time

import db
import game


def audit(conn, account_id, char_id, actor_type, actor_name, action, *, item_id=None,
          quantity=None, currency=None, amount=None, detail=""):
    conn.execute(
        "INSERT INTO account_audit(account_id,char_id,actor_type,actor_name,action,item_id,"
        "quantity,currency,amount,detail,created) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        (int(account_id), int(char_id) if char_id is not None else None, actor_type,
         actor_name or "", action, item_id, quantity, currency, amount,
         (detail or "")[:500], time.time()))


def history(conn, account_id, limit=100):
    rows = conn.execute(
        "SELECT aa.*,i.name AS item_name FROM account_audit aa "
        "LEFT JOIN items i ON i.item_id=aa.item_id WHERE aa.account_id=? "
        "ORDER BY aa.created DESC,aa.id DESC LIMIT ?", (int(account_id), min(int(limit), 250))).fetchall()
    return [{"id": int(r["id"]), "action": r["action"], "actorType": r["actor_type"],
             "actor": r["actor_name"], "itemId": r["item_id"], "item": r["item_name"],
             "quantity": r["quantity"], "currency": r["currency"], "amount": r["amount"],
             "detail": r["detail"], "created": float(r["created"])} for r in rows]


def search_players(conn, query, limit=40):
    q = (query or "").strip()
    like = f"%{q}%"
    rows = conn.execute(
        "SELECT a.id AS account_id,a.username,c.id AS char_id,c.name,c.level,c.gold,c.coins,"
        "c.upgrade_days,c.access_level FROM accounts a JOIN characters c ON c.account_id=a.id "
        "WHERE (?='' OR LOWER(a.username) LIKE LOWER(?) OR LOWER(c.name) LIKE LOWER(?) "
        "OR CAST(c.id AS TEXT)=?) ORDER BY c.name LIMIT ?",
        (q, like, like, q, min(int(limit), 100))).fetchall()
    return [{"accountId": int(r["account_id"]), "username": r["username"],
             "charId": int(r["char_id"]), "name": r["name"], "level": int(r["level"]),
             "gold": int(r["gold"]), "coins": int(r["coins"]),
             "membershipDays": int(r["upgrade_days"] or 0),
             "access": int(r["access_level"] or 0)} for r in rows]


def player(conn, char_id):
    row = conn.execute(
        "SELECT a.id AS account_id,a.username,a.created,a.last_accessed,c.* FROM accounts a "
        "JOIN characters c ON c.account_id=a.id WHERE c.id=?", (int(char_id),)).fetchone()
    if not row:
        return None
    inv = conn.execute(
        "SELECT ci.item_id,i.name,SUM(ci.quantity) quantity,MAX(ci.banked) banked "
        "FROM char_items ci JOIN items i ON i.item_id=ci.item_id WHERE ci.char_id=? "
        "GROUP BY ci.item_id,i.name ORDER BY i.name", (int(char_id),)).fetchall()
    return {"accountId": int(row["account_id"]), "charId": int(row["id"]),
            "username": row["username"], "name": row["name"], "level": int(row["level"]),
            "gold": int(row["gold"]), "coins": int(row["coins"]),
            "membershipDays": int(row["upgrade_days"] or 0),
            "inventory": [{"itemId": int(x["item_id"]), "name": x["name"],
                           "quantity": int(x["quantity"]), "banked": bool(x["banked"])} for x in inv],
            "history": history(conn, row["account_id"]),
            "redeemUses": [dict(x) for x in conn.execute(
                "SELECT code,description,redeemed_at FROM redeem_code_uses WHERE account_id=? "
                "ORDER BY redeemed_at DESC LIMIT 50", (row["account_id"],)).fetchall()]}


def grant(conn, actor, char_id, kind, value, quantity, reason):
    reason = (reason or "").strip()
    if len(reason) < 3:
        return False, "A support reason is required."
    char = conn.execute("SELECT * FROM characters WHERE id=?", (int(char_id),)).fetchone()
    if not char:
        return False, "Player not found."
    qty = max(1, min(int(quantity or 1), 10000))
    try:
        value = int(value)
        if kind in ("gold", "coins"):
            if value <= 0 or value > 10000000:
                return False, "Amount is outside the allowed range."
            conn.execute(f"UPDATE characters SET {kind}={kind}+? WHERE id=?", (value, char["id"]))
            audit(conn, char["account_id"], char["id"], "staff", actor, "staff_grant",
                  currency=kind, amount=value, detail=reason)
        elif kind == "membership":
            if value <= 0 or value > 3650:
                return False, "Membership days are outside the allowed range."
            conn.execute("UPDATE characters SET upgrade_days=upgrade_days+? WHERE id=?", (value, char["id"]))
            audit(conn, char["account_id"], char["id"], "staff", actor, "staff_grant",
                  currency="membership_days", amount=value, detail=reason)
        elif kind == "item":
            item = db.item(conn, value)
            if not item:
                return False, "Item not found."
            for _ in range(qty):
                game._grant_item(conn, char["id"], item)
            audit(conn, char["account_id"], char["id"], "staff", actor, "staff_grant",
                  item_id=value, quantity=qty, detail=reason)
        else:
            return False, "Unsupported grant type."
        conn.commit()
        return True, "Grant applied and recorded."
    except (TypeError, ValueError):
        conn.rollback()
        return False, "Invalid value."


def save_code(conn, actor, data):
    code = (data.get("code") or "").strip().upper()
    rewards = data.get("rewards") or []
    if not code or len(code) > 64 or not rewards:
        return False, "Code and at least one reward are required."
    clean = []
    for r in rewards:
        kind = str(r.get("type") or "").lower()
        if kind not in ("gold", "coins", "item", "achievement"):
            return False, "Unsupported reward type."
        value, qty = int(r.get("value")), max(1, min(int(r.get("quantity", 1)), 10000))
        if kind == "item" and db.item(conn, value) is None:
            return False, f"Item {value} was not found."
        clean.append((kind, value, qty, str(r.get("field") or "ip25")))
    conn.execute("INSERT INTO redeem_codes(code,description,max_uses,active,created) VALUES(?,?,?,?,?) "
                 "ON CONFLICT(code) DO UPDATE SET description=excluded.description,"
                 "max_uses=excluded.max_uses,active=excluded.active",
                 (code, str(data.get("description") or "")[:250], int(data.get("maxUses") or 0),
                  1 if data.get("active", True) else 0, time.time()))
    conn.execute("DELETE FROM redeem_code_rewards WHERE code=?", (code,))
    conn.executemany("INSERT INTO redeem_code_rewards(code,reward_type,reward_value,reward_qty,reward_field) "
                     "VALUES(?,?,?,?,?)", [(code, *x) for x in clean])
    # Global administrative event uses account 0, never a player's identity.
    audit(conn, 0, None, "staff", actor, "redeem_code_saved", detail=code)
    conn.commit()
    return True, "Redeem code saved."


def codes(conn):
    rows = conn.execute("SELECT * FROM redeem_codes ORDER BY created DESC,code").fetchall()
    out = []
    for r in rows:
        rewards = conn.execute("SELECT reward_type,reward_value,reward_qty,reward_field "
                               "FROM redeem_code_rewards WHERE code=? ORDER BY id", (r["code"],)).fetchall()
        uses = conn.execute("SELECT COUNT(*) n FROM redeem_code_uses WHERE code=?", (r["code"],)).fetchone()["n"]
        out.append({"code": r["code"], "description": r["description"], "maxUses": int(r["max_uses"]),
                    "active": bool(r["active"]), "uses": int(uses), "rewards": [dict(x) for x in rewards]})
    return out
