#!/usr/bin/env python3
r"""
Wire the house-buying flow into the two NPCs AE placed at/around buyhouse, both of which were
shipped inert (a WIP AE never finished):

  Penny (apop 116) — her "Buy a House" button (ID 33) is captured with action="Nothing" and
  a Level>=999 requirement, locked with "Housing coming soon". The client's real
  ApopButtonActions.OpenHouseInventory handler (decomp) does exactly what her button was
  clearly meant for: no house yet -> Area.moveToArea("buyhouse"); already own one ->
  UIHouseMenu.OpenStandalone(). This fixes the action + drops the requirement so it fires.

  Carl (apop 96, "House Builder", stationed at buyhouse) — has flavor dialogue but an EMPTY
  Buttons panel; nothing to click. Adds an ItemShop button (action="ItemShop", intMin=shopID)
  opening shop 2800 (build_buyhouse_shop.py) — the actual `loadShop` request, same mechanic
  every other AQW shop NPC uses (ApopButtonData.Execute: ItemShop -> RequestLoadShop(intMin)).

Idempotent: re-running just re-applies the same fixed shapes (keyed by button ID), and
writes both docs into data/apops.json (the seed source) so a fresh install ships them too —
seed_apops is INSERT-IF-ABSENT, so an EXISTING apops row (any DB that's already been seeded,
incl. prod) needs this script's direct UPDATE, not just a seed.run() pass.

Usage:
    python capture/fix_house_apops.py

Run locally against SQLite, or on the VM with .pg.env sourced for live Postgres.
"""
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "server"))
import db          # noqa: E402

APOPS_FILE = pathlib.Path(__file__).resolve().parent.parent / "data" / "apops.json"
PENNY_ID, CARL_ID = 116, 96
BUYHOUSE_SHOP_ID = 2800


def _find_button(doc, button_id):
    for panel in doc.get("panels") or []:
        for el in panel.get("elements") or []:
            if el.get("type") == "Button" and el.get("ID") == button_id:
                return el
    return None


def _buttons_panel(doc):
    for panel in doc.get("panels") or []:
        if panel.get("name") == "Buttons":
            return panel
    return None


def fix_penny(doc):
    btn = _find_button(doc, 33)
    if btn is None:
        print("  Penny: button 33 not found — apop shape changed, skipping")
        return False
    btn["action"] = "OpenHouseInventory"
    btn["requirements"] = []
    return True


def fix_carl(doc):
    if _find_button(doc, 90210) is not None:
        return True                          # already applied
    panel = _buttons_panel(doc)
    if panel is None:
        print("  Carl: no 'Buttons' panel found — apop shape changed, skipping")
        return False
    panel["elements"].append({
        "ID": 90210, "type": "Button", "targets": [], "acceptQuests": [], "turninQuests": [],
        "action": "ItemShop", "label": "Buy a House", "subtitle": "", "intMin": BUYHOUSE_SHOP_ID,
        "intMax": 0, "strData": "", "link": "", "text": "", "icon": "", "iconOverride": "none",
        "requirements": [], "reqCondition": "AND", "lockedIcon": "", "lockedLabel": "",
        "lockedMsg": "", "lockedMode": "Hide", "closePanels": False,
    })
    doc["nextElementId"] = max(int(doc.get("nextElementId") or 0), 90211)
    return True


def _load(conn, apop_id):
    row = conn.execute("SELECT raw FROM apops WHERE apop_id=?", (apop_id,)).fetchone()
    return json.loads(row["raw"]) if row else None


def _store(conn, apop_id, doc):
    conn.execute(
        "INSERT INTO apops(apop_id, name, raw) VALUES(?,?,?) "
        "ON CONFLICT(apop_id) DO UPDATE SET name=excluded.name, raw=excluded.raw",
        (apop_id, doc.get("name") or f"Apop {apop_id}",
         json.dumps(doc, separators=(",", ":"))))


def apply_to_db(conn):
    """Apply both fixes to whatever DB `conn` is on. Returns (penny_doc, carl_doc) — used by
    main() (which ALSO mirrors them into data/apops.json) and directly by tests (which must
    NOT touch the repo's data files as a side effect of running)."""
    penny = _load(conn, PENNY_ID)
    carl = _load(conn, CARL_ID)
    if penny is None or carl is None:
        sys.exit(f"apop {PENNY_ID} or {CARL_ID} missing from the DB — seed first")
    if fix_penny(penny):
        _store(conn, PENNY_ID, penny)
        print(f"  Penny (apop {PENNY_ID}): button 33 -> OpenHouseInventory, unlocked")
    if fix_carl(carl):
        _store(conn, CARL_ID, carl)
        print(f"  Carl (apop {CARL_ID}): 'Buy a House' -> shop {BUYHOUSE_SHOP_ID}")
    conn.commit()
    return penny, carl


def main():
    db.init()
    conn = db.connect()
    penny, carl = apply_to_db(conn)

    # mirror into data/apops.json (the seed source) so a fresh install ships this too
    apops = json.loads(APOPS_FILE.read_text(encoding="utf-8"))
    apops[str(PENNY_ID)] = penny
    apops[str(CARL_ID)] = carl
    APOPS_FILE.write_text(json.dumps(apops, separators=(",", ":")), encoding="utf-8")
    print(f"data/apops.json updated ({len(apops)} apops)")


if __name__ == "__main__":
    main()
