"""Exercise the additive migration on a disposable previous-version database."""
import os
import sqlite3
import subprocess
import sys
from pathlib import Path


def test_member_migration_preserves_existing_staff(tmp_path):
    database = tmp_path / "migration.db"
    with sqlite3.connect(database) as connection:
        connection.executescript("""
            CREATE TABLE staff (id INTEGER PRIMARY KEY, name VARCHAR(80), enabled BOOLEAN);
            INSERT INTO staff VALUES (1, 'Existing staff', 1);
            CREATE TABLE alembic_version (version_num VARCHAR(32) PRIMARY KEY);
            INSERT INTO alembic_version VALUES ('20260915_01');
        """)
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "20260928_01"],
        cwd=Path(__file__).resolve().parents[1],
        env={**os.environ, "TASK_DATABASE_URL": f"sqlite:///{database.as_posix()}"},
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT id, name, enabled, removed_at FROM staff").fetchall() == [(1, "Existing staff", 1, None)]
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "20260928_01"


def test_supervisor_migration_preserves_existing_staff_and_tasks(tmp_path):
    database = tmp_path / "supervisors.db"
    with sqlite3.connect(database) as connection:
        connection.executescript("""
            CREATE TABLE staff (id INTEGER PRIMARY KEY, name VARCHAR(80), enabled BOOLEAN, removed_at DATETIME);
            CREATE UNIQUE INDEX ix_staff_name ON staff (name);
            CREATE TABLE task (id INTEGER PRIMARY KEY, assignee_id INTEGER REFERENCES staff(id));
            INSERT INTO staff VALUES (1, 'Existing staff', 1, NULL), (2, 'Supervisor', 1, NULL);
            INSERT INTO task VALUES (10, 1);
            CREATE TABLE alembic_version (version_num VARCHAR(32) PRIMARY KEY);
            INSERT INTO alembic_version VALUES ('20261112_03');
        """)

    def migrate(action, revision):
        result = subprocess.run(
            [sys.executable, "-m", "alembic", action, revision],
            cwd=Path(__file__).resolve().parents[1],
            env={**os.environ, "TASK_DATABASE_URL": f"sqlite:///{database.as_posix()}"},
            capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 0, result.stderr

    migrate("upgrade", "20261117_01")
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT id, supervisor_id FROM staff ORDER BY id").fetchall() == [(1, None), (2, None)]
        assert connection.execute("SELECT * FROM task").fetchall() == [(10, 1)]
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("UPDATE staff SET supervisor_id = 2 WHERE id = 1")
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    migrate("downgrade", "20261112_03")
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT * FROM staff WHERE id = 1").fetchone() == (1, "Existing staff", 1, None)
        assert connection.execute("SELECT * FROM task").fetchall() == [(10, 1)]
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
