"""Store optional per-account desktop page grants."""

from alembic import op
import sqlalchemy as sa

revision = "20261110_01"
down_revision = "20261019_01"
branch_labels = None
depends_on = None


def upgrade():
    columns = sa.inspect(op.get_bind()).get_columns("user_account")
    if not any(column["name"] == "allowed_pages" for column in columns):
        op.add_column("user_account", sa.Column("allowed_pages", sa.JSON(none_as_null=True), nullable=True))


def downgrade():
    op.drop_column("user_account", "allowed_pages")
