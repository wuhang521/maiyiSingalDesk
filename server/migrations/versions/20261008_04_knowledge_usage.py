"""Paid recognition admission history survives original material removal."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.mysql import DATETIME

revision = "20261008_04"
down_revision = "20261008_03"
branch_labels = None
depends_on = None


def table():
    metadata = sa.MetaData()
    sa.Table("user_account", metadata, sa.Column("id", sa.Integer, primary_key=True))
    return sa.Table("knowledge_cloud_usage", metadata,
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("owner_id", sa.Integer, sa.ForeignKey("user_account.id"), nullable=False, index=True),
        sa.Column("operation", sa.String(30), nullable=False),
        sa.Column("created_at", sa.DateTime().with_variant(DATETIME(fsp=6), "mysql"), nullable=False, index=True))


def upgrade():
    table().create(op.get_bind(), checkfirst=True)


def downgrade():
    connection = op.get_bind()
    if connection.scalar(sa.text("SELECT COUNT(*) FROM knowledge_cloud_usage")):
        raise RuntimeError("Retain paid submission reservations during rollback")
    table().drop(connection, checkfirst=True)
