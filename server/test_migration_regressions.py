"""Complete SQLite-to-Postgres copy and safe refusal; only unique test schemas."""
import sqlite3

import pytest

import db
import game
import migrate_to_pg


@pytest.mark.skipif(db.BACKEND != 'postgres', reason='Requires PostgreSQL integration target')
def test_full_copy_plan_apply_and_occupied_refusal(tmp_path, monkeypatch):
    source_path = tmp_path / 'migration-source.db'
    with monkeypatch.context() as m:
        m.setattr(db, 'BACKEND', 'sqlite')
        m.setattr(db, 'DB_PATH', source_path)
        db.init()
        c = db.connect()
        try:
            char = game.login(c, 'migration_fixture', 'test-password')
            game.issue_token(c, char['account_id'])
            c.execute('INSERT INTO cutscenes(id,raw) VALUES(987654,?)', ('{"migration":true}',))
            c.commit()
        finally:
            c.close()
    source = sqlite3.connect(source_path)
    db.use_throwaway()
    dst = db.connect()
    try:
        planned = migrate_to_pg.migrate(source, dst)
        assert len(planned) == len(migrate_to_pg.TABLES)
        assert not dst.execute('SELECT tablename FROM pg_tables WHERE schemaname=current_schema()').fetchall()
        copied = migrate_to_pg.migrate(source, dst, apply=True)
        assert copied == planned
        assert dst.execute('SELECT raw FROM cutscenes WHERE id=987654').fetchone()[0] == '{"migration":true}'
        # Every generated identity, including newly added tables, must advance past imported IDs.
        new = game.login(dst, 'after_migration', 'test-password')
        assert new['id'] > char['id']
        with pytest.raises(ValueError, match='must be empty'):
            migrate_to_pg.migrate(source, dst, apply=True)
        assert dst.execute('SELECT COUNT(*) FROM accounts').fetchone()[0] == 2
    finally:
        source.close()
        dst.close()
