"""Persist the shared channel order and preserve history when a channel is removed."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql


revision = "20261112_01"
down_revision = "20261111_01"
branch_labels = None
depends_on = None


def upgrade():
    existing = {} if op.get_context().as_sql else {
        column["name"]: column for column in sa.inspect(op.get_bind()).get_columns("smart_channel")
    }
    columns = (
        ("display_order", sa.Integer(), sa.Integer),
        ("archived_at", sa.DateTime().with_variant(mysql.DATETIME(fsp=6), "mysql"), sa.DateTime),
    )
    for name, datatype, expected_type in columns:
        if name not in existing:
            op.add_column("smart_channel", sa.Column(name, datatype, nullable=True))
        elif not existing[name]["nullable"] or not isinstance(existing[name]["type"], expected_type):
            raise ValueError(f"Incompatible channel management column: {name}")


def downgrade():
    op.drop_column("smart_channel", "archived_at")
    op.drop_column("smart_channel", "display_order")
