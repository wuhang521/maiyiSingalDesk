"""Verify additive KB schema and MySQL-compatible types, without live credentials."""
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

from sqlalchemy import create_engine
from sqlalchemy.schema import CreateTable
from sqlalchemy.dialects import mysql

from app.database import Base
from app.knowledge_models import KnowledgeItem


def test_migration_preserves_previous_accounts_and_tasks(tmp_path):
    database = tmp_path / "kb-migration.db"
    engine = create_engine(f"sqlite:///{database.as_posix()}")
    for table in Base.metadata.sorted_tables:
        if not table.name.startswith("knowledge_"):
            table.create(engine)
    engine.dispose()
    with sqlite3.connect(database) as connection:
        connection.executescript("""
            INSERT INTO staff (id, name, enabled, created_at, updated_at) VALUES (1, 'Existing staff', 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP);
            CREATE TABLE alembic_version (version_num VARCHAR(32) PRIMARY KEY);
            INSERT INTO alembic_version VALUES ('20260928_01');
        """)
    for _ in range(2):
        result = subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"],
                                cwd=Path(__file__).resolve().parents[1],
                                env={**os.environ, "TASK_DATABASE_URL": f"sqlite:///{database.as_posix()}"},
                                capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, result.stderr
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT name FROM staff").fetchall() == [("Existing staff",)]
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "20261008_12"
        names = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert {"knowledge_item", "knowledge_asset", "knowledge_chunk", "knowledge_revision", "knowledge_job", "knowledge_nonce", "knowledge_file_cleanup", "knowledge_remote_cleanup", "knowledge_cloud_usage", "knowledge_organization", "knowledge_semantic_index"} <= names
        assert {"knowledge_team", "knowledge_team_member", "knowledge_share", "knowledge_team_event", "knowledge_ingress_receipt",
            "knowledge_preference", "knowledge_notification", "knowledge_worker_heartbeat", "knowledge_operational_alert",
            "knowledge_media_part", "knowledge_cost_reservation", "knowledge_provider_trace"} <= names
    ddl = str(CreateTable(KnowledgeItem.__table__).compile(dialect=mysql.dialect()))
    assert "LONGTEXT" in ddl and "uq_knowledge_source" in ddl
