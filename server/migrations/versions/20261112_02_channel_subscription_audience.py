"""Allow administrators to limit a channel to specified accounts."""

from alembic import op
import sqlalchemy as sa

revision = "20261112_02"
down_revision = "20261112_01"
branch_labels = None
depends_on = None


def upgrade():
    columns = [] if op.get_context().as_sql else sa.inspect(op.get_bind()).get_columns("smart_channel")
    existing = next((column for column in columns if column["name"] == "subscription_user_ids"), None)
    if existing is None:
        op.add_column("smart_channel", sa.Column("subscription_user_ids", sa.JSON(), nullable=True))
    elif not existing["nullable"] or not isinstance(existing["type"], sa.JSON):
        raise ValueError("Incompatible channel subscription audience column")


def downgrade():
    op.drop_column("smart_channel", "subscription_user_ids")
