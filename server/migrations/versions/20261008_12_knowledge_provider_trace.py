"""Provider request IDs and numeric usage, never content or guessed currency."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.mysql import DATETIME
revision = "20261008_12"
down_revision = "20261008_11"
branch_labels = None
depends_on = None
def table():
    m = sa.MetaData()
    sa.Table("knowledge_cloud_usage", m, sa.Column("id", sa.String(40), primary_key=True))
    return sa.Table("knowledge_provider_trace", m,
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("usage_id", sa.String(40), sa.ForeignKey("knowledge_cloud_usage.id"), nullable=False, index=True),
        sa.Column("provider", sa.String(30), nullable=False), sa.Column("request_id", sa.String(160), nullable=False),
        sa.Column("response_id", sa.String(160), nullable=False), sa.Column("http_status", sa.Integer, nullable=False),
        sa.Column("usage", sa.JSON, nullable=False),
        sa.Column("created_at", sa.DateTime().with_variant(DATETIME(fsp=6), "mysql"), nullable=False))
def upgrade(): table().create(op.get_bind(), checkfirst=True)
def downgrade():
    value = table()
    if op.get_bind().scalar(sa.select(sa.func.count()).select_from(value)): raise RuntimeError("Preserve provider bill-correlation metadata")
    value.drop(op.get_bind(), checkfirst=True)
