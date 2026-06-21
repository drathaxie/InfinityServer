"""
Web API login identity — per-player login (the staleness fix).

Ground truth (decomp UILoginActions.Login + onWebDataReceived, deployed client):
  - the client POSTs the TYPED username as form field `user` to login/nowinfinity,
  - then sends the game server Login[const, loginData.account.unm, sToken].
So the game-server username == whatever WE put in account.unm. The bug was that we
echoed the captured account ("Drathaxie") for everyone; the fix echoes the typed name
and get-or-creates that user's own character. Proven: two different usernames resolve
to two different characters, and the same username is stable across logins.
"""
import pathlib
import tempfile
import db
import seed
import game
import webapi


def main():
    db.use_throwaway()
    seed.run()
    conn = db.connect()

    def login(name):
        # the client posts urlencoded form fields; parse_qs yields {key:[val]}
        return webapi.login_nowinfinity(conn, {"user": [name], "pass": ["pw"]})

    # a fresh username gets ITS OWN identity, not the captured Drathaxie
    a = login("suswolf")
    assert a["bSuccess"], a
    assert a["account"]["unm"] == "suswolf", \
        f"account.unm must be the typed name (the game server reads it), got {a['account']['unm']}"
    assert a["account"]["chars"][0]["Name"] == "suswolf", "CharSelect shows the typed name"
    # sToken is now a real random session token (not the old predictable "local-<user>"), and it
    # gates the game-server Login: only this token resolves the session.
    tok = a["account"]["sToken"]
    assert tok and tok != "local-suswolf" and len(tok) >= 32, f"sToken must be a random session token, got {tok!r}"
    assert game.resolve_session(conn, "suswolf", tok) is not None, "the issued token resolves the game session"
    assert game.resolve_session(conn, "suswolf", "local-suswolf") is None, "the old predictable token is rejected"
    # the server list always points at our local game server
    assert a["servers"][0]["sIP"] == "127.0.0.1" and a["servers"][0]["iPort"] == 5588

    # a DIFFERENT username is a DIFFERENT character (true multi-account)
    b = login("zemonx")
    assert b["account"]["unm"] == "zemonx"
    assert b["account"]["chars"][0]["charid"] != a["account"]["chars"][0]["charid"], \
        "distinct usernames must be distinct characters"

    # the CharSelect numbers come from OUR DB for that user, not the captured template
    cw = game.login(conn, "suswolf", "pw")
    assert a["account"]["iGold"] == cw["gold"] and a["account"]["iLevel"] == cw["level"], \
        "CharSelect gold/level reflect the user's own DB row, not the captured Drathaxie"

    # the CharSelect preview is rebuilt from the user's OWN character, not the captured template
    ca = a["account"]["chars"][0]
    cb = b["account"]["chars"][0]
    cw = game.login(conn, "suswolf", "pw")
    assert ca["intLevel"] == cw["level"] and ca["mobileGold"] == cw["gold"], "preview level/gold from DB"
    assert ca["customization"]["SkinColor"] == cw["skin_color"], "preview colours from the user's char"
    # two different users get DIFFERENT seeded colours (own appearance, not a shared template)
    assert ca["customization"]["SkinColor"] != cb["customization"]["SkinColor"], \
        "distinct users have distinct avatars (own colour set)"
    assert ca["intHPMax"] == game.build_combat_stats(cw, game.pattern_bonus(conn, cw["id"]))[1], \
        "preview HP matches the stat-derived MaxHP"

    # same username is STABLE across logins (idempotent get-or-create)
    a2 = login("suswolf")
    assert a2["account"]["chars"][0]["charid"] == a["account"]["chars"][0]["charid"], \
        "re-login as the same user returns the same character"

    # empty username degrades to a default rather than crashing
    h = login("")
    assert h["account"]["unm"] == "Hero"

    print(f"webapi login OK: suswolf->char#{a['account']['chars'][0]['charid']}, "
          f"zemonx->char#{b['account']['chars'][0]['charid']} (distinct), "
          f"gold/level from DB, stable across relogin")
    print("ALL WEBAPI TESTS PASSED")


if __name__ == "__main__":
    main()
