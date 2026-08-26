import db, placements
conn = db.connect()
before = conn.execute("SELECT COUNT(*) AS n FROM map_pads WHERE map=?", ("graveyard",)).fetchone()["n"]
placements.take_over(conn, "graveyard", force=True)
after = conn.execute("SELECT COUNT(*) AS n FROM map_pads WHERE map=?", ("graveyard",)).fetchone()["n"]
frames = {}
for r in conn.execute("SELECT frame FROM map_pads WHERE map=?", ("graveyard",)).fetchall():
    frames[r["frame"]] = frames.get(r["frame"], 0) + 1
print(f"map_pads before={before} after={after}")
print("by frame:", frames)
