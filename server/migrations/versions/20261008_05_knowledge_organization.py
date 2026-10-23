"""Version-bound, separate AI notes; no changes to existing original text."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.mysql import DATETIME

revision = "20261008_05"
down_revision = "20261008_04"
branch_labels = None
depends_on = None


def table():
    m = sa.MetaData()
    sa.Table("knowledge_item", m, sa.Column("id", sa.String(40), primary_key=True))
    return sa.Table("knowledge_organization", m,
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("item_id", sa.String(40), sa.ForeignKey("knowledge_item.id"), nullable=False, index=True),
        sa.Column("source_revision", sa.Integer, nullable=False),
        sa.Column("state", sa.String(30), nullable=False),
        sa.Column("payload", sa.JSON, nullable=False),
        sa.Column("status_note", sa.String(500), nullable=False),
        sa.Column("provider", sa.String(80), nullable=False),
        sa.Column("model", sa.String(200), nullable=False),
        sa.Column("created_at", sa.DateTime().with_variant(DATETIME(fsp=6), "mysql"), nullable=False, index=True))


def upgrade():
    table().create(op.get_bind(), checkfirst=True)


def downgrade():
    if op.get_bind().scalar(sa.text("SELECT COUNT(*) FROM knowledge_organization")):
        raise RuntimeError("Retain saved AI notes during rollback")
    table().drop(op.get_bind(), checkfirst=True)
