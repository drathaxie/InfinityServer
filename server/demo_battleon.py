#!/usr/bin/env python3
"""
Decisive in-game test of the authored-placement layer, against the LIVE DB.

  python server/demo_battleon.py apply    # take over battleon: remove Artix, add a Twilly
  python server/demo_battleon.py revert    # hand battleon back to the captured roster
  python server/demo_battleon.py status    # show whether battleon is authored + its roster

After `apply`, reload/return to Battleon in-game: Artix Krieger should be GONE and an
extra Twilly should stand near spawn. That proves the server's monBranch fully controls
which NPCs exist (suppress + add), persisted to SQLite. `revert` undoes it.
"""
import sys

import db
import placements
import maps

MAP = "battleon"
ARTIX_PAD = 178     # Artix Krieger (MonID 54)
TWILLY = 168        # art already present in battleon, so it always renders


def roster(conn):
    area = maps.area_payload(MAP, conn)
    return area["monBranch"] if area else []


def show(conn, label):
    branch = roster(conn)
    authored = placements.is_authored(conn, MAP)
    print(f"[{label}] battleon authored={authored}  NPCs={len(branch)}")
    for b in branch:
        print(f"    MonMapID {b['MonMapID']:>6}  MonID {b['MonID']:>4}  "
              f"{b.get('strMonName','')}  @({b.get('x')},{b.get('y')}) {b.get('strFrame')}")


def apply(conn):
    show(conn, "before")
    placements.take_over(conn, MAP, force=True)        # reseed clean from capture
    placements.pad_delete(conn, MAP, ARTIX_PAD)        # remove Artix
    pad = placements.add_new_pad(
        conn, MAP, '{"X": 8.0, "Y": -1.3, "Frame": "Enter", "Direction": -1}')
    placements.add_mon(conn, MAP, TWILLY, pad)          # add a Twilly next to spawn
    print(f"\napplied: removed Artix (pad {ARTIX_PAD}), added MonID {TWILLY} on pad {pad}\n")
    show(conn, "after")
    print("\n>>> Reload Battleon in-game: Artix gone, extra Twilly near spawn.")


def revert(conn):
    conn.execute("DELETE FROM map_pads WHERE map=?", (MAP,))
    conn.execute("DELETE FROM map_state WHERE map=?", (MAP,))
    conn.commit()
    show(conn, "reverted")
    print("\n>>> Reload Battleon in-game: original captured roster restored.")


def main():
    action = sys.argv[1] if len(sys.argv) > 1 else "status"
    db.init()
    conn = db.connect()
    if action == "apply":
        apply(conn)
    elif action == "revert":
        revert(conn)
    else:
        show(conn, "status")


if __name__ == "__main__":
    main()
