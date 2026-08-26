import db
conn = db.connect()
row = conn.execute("SELECT apop_id FROM apops WHERE apop_id=?", (199,)).fetchone()
print("apop 199 in DB:", dict(row) if row else "MISSING")

pads = conn.execute(
    "SELECT p.pad_id, p.frame, n.mon_id, n.name, n.apop_id "
    "FROM map_pads p JOIN pad_npcs n ON n.map=p.map AND n.pad_id=p.pad_id "
    "WHERE p.map=? AND n.mon_id=?", ("graveyard", 428)).fetchall()
for r in pads:
    print(dict(r))
