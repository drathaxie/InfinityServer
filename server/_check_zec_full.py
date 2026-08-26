import db, placements, json
conn = db.connect()
mb = placements.compiled_monbranch(conn, "graveyard")
zec = [m for m in mb if m.get("MonID") == 428]
print(json.dumps(zec, indent=2, default=str))
