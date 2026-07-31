import db, traceback
db.use_throwaway(); c=db.connect()
stmts = db._split_statements(db._schema_sql())
items_stmt = [s for s in stmts if 'CREATE TABLE IF NOT EXISTS items ' in s][0]
create_only = items_stmt[items_stmt.index('CREATE TABLE'):]
print("tail repr:", repr(create_only[-50:]))
print("has '%':", '%' in create_only, "| has '?':", '?' in create_only)
try:
    c.execute(create_only)
    print("no exception; to_regclass items =", c.execute("SELECT to_regclass('items') AS t").fetchone()["t"])
except Exception as e:
    print("EXCEPTION:", type(e).__name__, "|", str(e)[:400])
    traceback.print_exc()
