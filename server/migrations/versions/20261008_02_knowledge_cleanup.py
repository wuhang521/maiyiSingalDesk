"""Durable file deletion intents; preserve all existing knowledge records."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.mysql import DATETIME

revision = "20261008_02"
down_revision = "20261008_01"
branch_labels = None
depends_on = None


def table():
    dt = sa.DateTime().with_variant(DATETIME(fsp=6), "mysql")
    return sa.Table("knowledge_file_cleanup", sa.MetaData(),
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("storage_name", sa.String(80), nullable=False, unique=True),
        sa.Column("attempts", sa.Integer, nullable=False),
        sa.Column("available_at", dt, nullable=False, index=True),
        sa.Column("created_at", dt, nullable=False))


def upgrade():
    table().create(op.get_bind(), checkfirst=True)


def downgrade():
    # Do not discard pending deletion intents during a live rollback.
    connection = op.get_bind()
    if connection.scalar(sa.text("SELECT COUNT(*) FROM knowledge_file_cleanup")):
        raise RuntimeError("Pending knowledge file cleanup: retain this table during rollback")
    table().drop(connection, checkfirst=True)
