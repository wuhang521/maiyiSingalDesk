"""Bounded, hashed attachment ingress receipts independent of document deletion."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.mysql import DATETIME

revision = "20261008_08"
down_revision = "20261008_07"
branch_labels = None
depends_on = None


def table():
    metadata = sa.MetaData()
    sa.Table("user_account", metadata, sa.Column("id", sa.Integer, primary_key=True))
    return sa.Table("knowledge_ingress_receipt", metadata,
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("owner_id", sa.Integer, sa.ForeignKey("user_account.id"), nullable=False, index=True),
        sa.Column("item_id", sa.String(40), nullable=False), sa.Column("state", sa.String(30), nullable=False),
        sa.Column("created_at", sa.DateTime().with_variant(DATETIME(fsp=6), "mysql"), nullable=False))


def upgrade(): table().create(op.get_bind(), checkfirst=True)


def downgrade():
    if op.get_bind().scalar(sa.select(sa.func.count()).select_from(table())):
        raise RuntimeError("Retain ingress deduplication history")
    table().drop(op.get_bind(), checkfirst=True)
