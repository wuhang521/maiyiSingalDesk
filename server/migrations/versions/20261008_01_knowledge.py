"""Add private knowledge storage without changing existing task/content tables."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.mysql import DATETIME, LONGTEXT

revision = "20261008_01"
down_revision = "20260928_01"
branch_labels = None
depends_on = None


def tables():
    """Frozen schema snapshot: never import evolving application ORM models."""
    m = sa.MetaData()
    sa.Table("user_account", m, sa.Column("id", sa.Integer, primary_key=True))
    large = sa.Text().with_variant(LONGTEXT(), "mysql")
    dt = sa.DateTime().with_variant(DATETIME(fsp=6), "mysql")
    item = sa.Table("knowledge_item", m,
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("owner_id", sa.Integer, sa.ForeignKey("user_account.id"), nullable=False, index=True),
        sa.Column("source_key", sa.String(64), nullable=True),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("source_url", sa.Text, nullable=False),
        sa.Column("kind", sa.String(30), nullable=False),
        sa.Column("publisher", sa.String(200), nullable=False),
        sa.Column("tags", sa.JSON, nullable=False),
        sa.Column("status", sa.String(30), nullable=False, index=True),
        sa.Column("status_note", sa.String(500), nullable=False),
        sa.Column("revision", sa.Integer, nullable=False),
        sa.Column("content", large, nullable=False),
        sa.Column("summary", sa.Text, nullable=False),
        sa.Column("method", sa.String(40), nullable=False),
        sa.Column("deleted_at", dt, nullable=True),
        sa.Column("created_at", dt, nullable=False),
        sa.Column("updated_at", dt, nullable=False),
        sa.UniqueConstraint("owner_id", "source_key", name="uq_knowledge_source"))
    asset = sa.Table("knowledge_asset", m,
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("item_id", sa.String(40), sa.ForeignKey("knowledge_item.id"), nullable=False, index=True),
        sa.Column("filename", sa.String(200), nullable=False),
        sa.Column("storage_name", sa.String(80), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("size", sa.Integer, nullable=False),
        sa.Column("created_at", dt, nullable=False),
        sa.UniqueConstraint("item_id", "sha256", name="uq_knowledge_asset"))
    chunk = sa.Table("knowledge_chunk", m,
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("item_id", sa.String(40), sa.ForeignKey("knowledge_item.id"), nullable=False, index=True),
        sa.Column("revision", sa.Integer, nullable=False),
        sa.Column("ordinal", sa.Integer, nullable=False),
        sa.Column("locator", sa.String(100), nullable=False),
        sa.Column("content", sa.Text, nullable=False))
    revision_table = sa.Table("knowledge_revision", m,
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("item_id", sa.String(40), sa.ForeignKey("knowledge_item.id"), nullable=False, index=True),
        sa.Column("revision", sa.Integer, nullable=False),
        sa.Column("content", large, nullable=False),
        sa.Column("method", sa.String(40), nullable=False),
        sa.Column("created_at", dt, nullable=False),
        sa.UniqueConstraint("item_id", "revision", name="uq_knowledge_revision"))
    job = sa.Table("knowledge_job", m,
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("item_id", sa.String(40), sa.ForeignKey("knowledge_item.id"), nullable=False, index=True),
        sa.Column("revision", sa.Integer, nullable=False),
        sa.Column("asset_id", sa.String(40), nullable=True),
        sa.Column("state", sa.String(30), nullable=False, index=True),
        sa.Column("attempts", sa.Integer, nullable=False),
        sa.Column("lease_token", sa.String(40), nullable=True),
        sa.Column("lease_until", dt, nullable=True),
        sa.Column("available_at", dt, nullable=False),
        sa.Column("created_at", dt, nullable=False))
    nonce = sa.Table("knowledge_nonce", m,
        sa.Column("nonce", sa.String(64), primary_key=True),
        sa.Column("created_at", dt, nullable=False, index=True))
    return (item, asset, chunk, revision_table, job, nonce)


def upgrade():
    for table in tables():
        table.create(op.get_bind(), checkfirst=True)


def downgrade():
    # This discards knowledge records; restore a backup instead on a live deployment.
    connection = op.get_bind()
    for table in reversed(tables()):
        table.drop(connection, checkfirst=True)
