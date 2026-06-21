"""
Account auth (OUR design — there is no AE capture of credential handling). Passwords are
PBKDF2-hashed and validated; a wrong password is rejected; a legacy plaintext password is accepted
once and upgraded to a hash; and the game-server Login is gated by an API-issued session token, not
by re-sending the password (so a direct TCP connection can't impersonate an account).
"""
import db
import game


def main():
    db.use_throwaway()
    db.init()
    conn = db.connect()

    # first login registers the account; the password is stored hashed, never plaintext
    alice = game.login(conn, "alice", "secret")
    assert alice is not None, "first login registers the account"
    stored = conn.execute("SELECT password FROM accounts WHERE LOWER(username)='alice'").fetchone()["password"]
    assert stored.startswith("pbkdf2_sha256$") and "secret" not in stored, "password stored as a hash"

    # a wrong password is REJECTED; the correct one is accepted
    assert game.login(conn, "alice", "wrong") is None, "wrong password rejected"
    assert game.login(conn, "alice", "secret") is not None, "correct password accepted"

    # a legacy plaintext row is accepted once (correct value) and upgraded to a hash
    conn.execute("INSERT INTO accounts(username, password, created) VALUES('bob', 'plain123', 0)")
    conn.commit()
    assert game.login(conn, "bob", "nope") is None, "legacy: wrong password still rejected"
    assert game.login(conn, "bob", "plain123") is not None, "legacy: correct plaintext accepted"
    up = conn.execute("SELECT password FROM accounts WHERE LOWER(username)='bob'").fetchone()["password"]
    assert up.startswith("pbkdf2_sha256$"), "legacy plaintext upgraded to a hash on login"

    # game-server token gate: only a valid API-issued token resolves a session
    tok = game.issue_token(conn, alice["account_id"])
    assert game.resolve_session(conn, "alice", tok) is not None, "valid token resolves the session"
    assert game.resolve_session(conn, "alice", "deadbeef") is None, "bogus token rejected"
    assert game.resolve_session(conn, "alice", "") is None, "empty token rejected"
    assert game.resolve_session(conn, "alice", "local-alice") is None, "old predictable token no longer works"

    # a fresh issue rotates the token (the previous one stops working)
    tok2 = game.issue_token(conn, alice["account_id"])
    assert tok2 != tok and game.resolve_session(conn, "alice", tok) is None, "re-issue rotates the token"
    assert game.resolve_session(conn, "alice", tok2) is not None

    print("auth OK: hashed passwords, wrong password rejected, legacy upgrade, token-gated game login")
    print("ALL AUTH TESTS PASSED")


if __name__ == "__main__":
    main()
