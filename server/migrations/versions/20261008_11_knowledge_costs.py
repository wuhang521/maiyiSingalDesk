"""Configured monetary reservations and explicitly reconciled provider charges."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.mysql import DATETIME
revision = "20261008_11"
down_revision = "20261008_10"
branch_labels = None
depends_on = None

def table():
    m = sa.MetaData()
    sa.Table("knowledge_cloud_usage", m, sa.Column("id", sa.String(40), primary_key=True))
    sa.Table("user_account", m, sa.Column("id", sa.Integer, primary_key=True))
    return sa.Table("knowledge_cost_reservation", m,
        sa.Column("usage_id", sa.String(40), sa.ForeignKey("knowledge_cloud_usage.id"), primary_key=True),
        sa.Column("reserved_micros", sa.BigInteger, nullable=True), sa.Column("actual_micros", sa.BigInteger, nullable=True),
        sa.Column("invoice_reference", sa.String(160), nullable=True, unique=True),
        sa.Column("reconciled_by", sa.Integer, sa.ForeignKey("user_account.id"), nullable=True),
        sa.Column("reconciled_at", sa.DateTime().with_variant(DATETIME(fsp=6), "mysql"), nullable=True))

def upgrade(): table().create(op.get_bind(), checkfirst=True)
def downgrade():
    value = table()
    if op.get_bind().scalar(sa.select(sa.func.count()).select_from(value)): raise RuntimeError("Preserve billing admission and reconciliation")
    value.drop(op.get_bind(), checkfirst=True)
