"""Add explicit OCR jobs and independent provider-file cleanup, without requeueing."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.mysql import DATETIME

revision = "20261008_03"
down_revision = "20261008_02"
branch_labels = None
depends_on = None


def remote_table():
    dt = sa.DateTime().with_variant(DATETIME(fsp=6), "mysql")
    return sa.Table("knowledge_remote_cleanup", sa.MetaData(),
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("provider_file_id", sa.String(160), nullable=False, unique=True),
        sa.Column("attempts", sa.Integer, nullable=False),
        sa.Column("available_at", dt, nullable=False, index=True),
        sa.Column("created_at", dt, nullable=False))


def upgrade():
    if "mode" not in {c["name"] for c in sa.inspect(op.get_bind()).get_columns("knowledge_job")}:
        op.add_column("knowledge_job", sa.Column("mode", sa.String(30), nullable=False, server_default="local"))
    remote_table().create(op.get_bind(), checkfirst=True)


def downgrade():
    connection = op.get_bind()
    if connection.scalar(sa.text("SELECT COUNT(*) FROM knowledge_remote_cleanup")):
        raise RuntimeError("Pending OCR cloud-file cleanup: retain table during rollback")
    if connection.scalar(sa.text("SELECT COUNT(*) FROM knowledge_job WHERE mode <> 'local'")):
        raise RuntimeError("Retain OCR job history during rollback")
    remote_table().drop(connection, checkfirst=True)
    op.drop_column("knowledge_job", "mode")
