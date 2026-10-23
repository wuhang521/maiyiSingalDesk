"""Retain removed members without deleting tasks, accounts or review history."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

revision = "20260928_01"
down_revision = "20260915_01"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("staff", sa.Column("removed_at", sa.DateTime().with_variant(mysql.DATETIME(fsp=6), "mysql"), nullable=True))


def downgrade():
    op.drop_column("staff", "removed_at")
