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
