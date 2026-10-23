"""Immutable media recognitions for explicit source-bound merge."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.mysql import DATETIME, LONGTEXT
revision = "20261008_10"
down_revision = "20261008_09"
branch_labels = None
depends_on = None

def table():
    m = sa.MetaData()
    sa.Table("knowledge_item", m, sa.Column("id", sa.String(40), primary_key=True))
    return sa.Table("knowledge_media_part", m,
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("item_id", sa.String(40), sa.ForeignKey("knowledge_item.id"), nullable=False, index=True),
        sa.Column("asset_id", sa.String(40), nullable=False), sa.Column("source_revision", sa.Integer, nullable=False),
        sa.Column("method", sa.String(40), nullable=False),
        sa.Column("content", sa.Text().with_variant(LONGTEXT(), "mysql"), nullable=False),
        sa.Column("chunks", sa.JSON, nullable=False),
        sa.Column("created_at", sa.DateTime().with_variant(DATETIME(fsp=6), "mysql"), nullable=False),
        sa.UniqueConstraint("item_id", "source_revision", name="uq_knowledge_media_part"))

def upgrade(): table().create(op.get_bind(), checkfirst=True)
def downgrade():
    value = table()
    if op.get_bind().scalar(sa.select(sa.func.count()).select_from(value)): raise RuntimeError("Preserve media recognition history")
    value.drop(op.get_bind(), checkfirst=True)
