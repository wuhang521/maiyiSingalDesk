"""Opt-in personal semantic projection, additive and independently removable."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.mysql import DATETIME

revision = "20261008_06"
down_revision = "20261008_05"
branch_labels = None
depends_on = None


def table():
    metadata = sa.MetaData()
    sa.Table("knowledge_item", metadata, sa.Column("id", sa.String(40), primary_key=True))
    return sa.Table("knowledge_semantic_index", metadata,
        sa.Column("item_id", sa.String(40), sa.ForeignKey("knowledge_item.id"), primary_key=True),
        sa.Column("source_revision", sa.Integer, nullable=False),
        sa.Column("request_id", sa.String(40), nullable=False),
        sa.Column("state", sa.String(30), nullable=False),
        sa.Column("model", sa.String(200), nullable=False),
        sa.Column("resolved_model", sa.String(200), nullable=False),
        sa.Column("chunk_count", sa.Integer, nullable=False),
        sa.Column("payload", sa.JSON, nullable=False),
        sa.Column("created_at", sa.DateTime().with_variant(DATETIME(fsp=6), "mysql"), nullable=False))


def upgrade():
    table().create(op.get_bind(), checkfirst=True)


def downgrade():
    if op.get_bind().scalar(sa.text("SELECT COUNT(*) FROM knowledge_semantic_index")):
        raise RuntimeError("Remove semantic projections explicitly before downgrade")
    table().drop(op.get_bind(), checkfirst=True)
