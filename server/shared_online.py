"""One live socket per shared account, across every server using this MariaDB.

A dedicated, unpooled connection owns a named lock until socket cleanup finishes.
It never reconnects: losing the DB connection invalidates the game session.
"""
import os
import db

MESSAGE = "This account is already online. Log out of your other game or server first."


def enabled():
    return db.BACKEND in ("mysql", "mariadb")


class Lease:
    def __init__(self, conn, name):
        self.conn, self.name = conn, name
        self.closed = False

    def valid(self):
        if self.closed:
            return False
        if self.conn is None:
            return True
        try:
            with self.conn.cursor() as cur:
                cur.execute("SELECT IS_USED_LOCK(%s) = CONNECTION_ID()", (self.name,))
                return cur.fetchone()[0] == 1
        except Exception:
            return False

    def close(self):
        if self.closed:
            return
        self.closed = True
        if self.conn is not None:
            try:
                self.conn.close()  # session-scoped locks release even after a process crash
            except Exception:
                pass  # ownership is already gone when the connection has failed


def acquire(account_id):
    if not enabled():
        return Lease(None, "")  # legacy single-game SQLite/Postgres mode
    import pymysql
    conn = pymysql.connect(
        host=os.environ.get("INFINITY_MYSQL_HOST", "127.0.0.1"),
        port=int(os.environ.get("INFINITY_MYSQL_PORT", "3306")),
        database=db._MY_DB or os.environ.get("INFINITY_MYSQL_DB", "aqwworlds"),
        user=os.environ.get("INFINITY_MYSQL_USER", "swordshine"),
        password=os.environ.get("INFINITY_MYSQL_PASSWORD", ""),
        autocommit=True, connect_timeout=5, read_timeout=5, write_timeout=5,
    )
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT CONCAT('account-online:', DATABASE(), ':', %s)", (int(account_id),))
            name = cur.fetchone()[0]
            cur.execute("SELECT GET_LOCK(%s, 0)", (name,))
            if cur.fetchone()[0] != 1:
                conn.close()
                return None
        return Lease(conn, name)
    except BaseException:
        conn.close()
        raise
