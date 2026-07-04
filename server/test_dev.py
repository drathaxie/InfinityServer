"""
Dev/staff access + the Dialogger (cutscene editor) DB round-trip.

- An account listed in data/dev_users.txt logs in with access_level 50 (unlocks the in-game
  authoring tools: Dialogger editor, apop "+"/editor, charedit, /cutscene, /devon...); everyone
  else stays a normal player (0).
- DialoggerSave/DialoggerLoad (the editor's save/load buttons) round-trip the cutscene JSON
  through our webapi, matching AE's contract ({staff,s_key,id,json} -> id ; {id} -> json).
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

    # point the dev allowlist at a temp file we control (don't touch the real config)
    devfile = pathlib.Path(tempfile.mkdtemp()) / "dev_users.txt"
    devfile.write_text("# test\n__staff__\n", encoding="utf-8")
    game._DEV_USERS_FILE = devfile

    staff = game.login(conn, "__staff__", "pw")
    assert staff["access_level"] == game.DEV_ACCESS_LEVEL, "listed account gets dev access on login"
    player = game.login(conn, "__player__", "pw")
    assert int(player["access_level"] or 0) == 0, "an unlisted account stays a normal player"

    # access surfaces in initPlayer so the client enables the dev UI
    init = game.build_init_player(conn, staff)
    assert init["user"]["intAccessLevel"] == game.DEV_ACCESS_LEVEL, "initPlayer carries dev access"

    # Dialogger editor: Save (new) -> returns an id; Load(id) -> the same JSON back
    import urllib.parse
    blob = '{"ID":0,"name":"Test Cutscene","panels":[]}'
    save = webapi.dialogger_save(conn, urllib.parse.parse_qs(
        f"staff=__staff__&s_key=x&id=&json={urllib.parse.quote(blob)}"))
    cid = int(save)
    assert cid > 0, f"DialoggerSave returns the new cutscene id, got {save!r}"
    loaded = webapi.dialogger_load(conn, urllib.parse.parse_qs(f"id={cid}"))
    assert loaded == blob, "DialoggerLoad returns the saved JSON verbatim"
    # saving with that id UPDATES it (not a new row)
    blob2 = '{"ID":%d,"name":"Edited","panels":[]}' % cid
    save2 = webapi.dialogger_save(conn, urllib.parse.parse_qs(
        f"staff=__staff__&s_key=x&id={cid}&json={urllib.parse.quote(blob2)}"))
    assert int(save2) == cid and webapi.dialogger_load(conn, urllib.parse.parse_qs(f"id={cid}")) == blob2

    # playback: /cutscene <id> -> getDialog serves the SAME saved cutscene (no sample echo)
    assert game.load_dialog(conn, cid) == blob2, "getDialog plays the saved cutscene from our store"
    # An unknown cutscene serves a minimal fade-and-complete scene, NOT "" — an empty scene hangs
    # the client's player forever (PR #9). The quest/storyline step that triggered it then advances.
    import json as _cj
    unknown = game.load_dialog(conn, 999999)
    assert unknown == game._MINIMAL_CUTSCENE, "unknown cutscene -> minimal fade-and-complete scene"
    assert _cj.loads(unknown)["frames"][0][0] == "FadeToBlack", "minimal scene fades and completes"

    # the seeded captured AE cutscene (Bludrut Title Splash, id 28) plays
    import json as _json
    bludrut = game.load_dialog(conn, 28)
    assert bludrut, "captured cutscene 28 is seeded and playable via getDialog"
    assert _json.loads(bludrut).get("cutsceneName") == "Bludrut Title Splash", "real captured cutscene"

    # asset-bundle resolver: the client does IDs.Select(id => cache[id]); if we omit ANY requested
    # id it throws and the cutscene HANGS on "Loading Cutscene Assets...". So GetAssetBundlesByIDs
    # must return EVERY requested id — resolved or a harmless stub.
    webapi._resolve_bundles_upstream = lambda ids: None        # no network in tests
    webapi._bundles[66131] = {"ID": 66131, "Name": "x", "Filename": "f.unity3d"}
    res = webapi.get_asset_bundles(conn, "ids=66131,999998")
    assert [b["ID"] for b in res] == [66131, 999998], "every requested bundle id is returned"
    assert res[1]["Filename"] == "", "an unresolved id returns a stub (no KeyError -> no client hang)"

    print(f"dev OK: staff access {staff['access_level']} (player {player['access_level'] or 0}); "
          f"Dialogger save/load round-trips + getDialog playback (cutscene #{cid})")
    print("ALL DEV TESTS PASSED")


if __name__ == "__main__":
    main()
