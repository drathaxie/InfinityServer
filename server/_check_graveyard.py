import db, json
conn = db.connect()
row = conn.execute("SELECT doc FROM maps WHERE str_map_name=?", ("graveyard",)).fetchone()
d = json.loads(row["doc"])
print("Bundle:", d["area"]["Bundle"])
print("monBranch:", len(d["area"]["monBranch"]))
