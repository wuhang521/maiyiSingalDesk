"""Store explicit channel subscriptions and their WeChat delivery records."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

revision = "20261111_01"
down_revision = "20261110_01"
branch_labels = None
depends_on = None

datetime6 = sa.DateTime().with_variant(mysql.DATETIME(fsp=6), "mysql")
CHANNEL_TABLES = {
    "channel_push_config", "channel_subscription", "channel_push_delivery", "channel_website_snapshot",
}


def channel_key(*, primary_key=False):
    return sa.Column("channel_id", sa.String(80), sa.ForeignKey("smart_channel.id", ondelete="CASCADE"),
                     nullable=False, primary_key=primary_key)


def user_key(*, primary_key=False):
    return sa.Column("user_id", sa.Integer, sa.ForeignKey("user_account.id", ondelete="CASCADE"),
                     nullable=False, primary_key=primary_key)


def upgrade():
    # The production compatibility release can apply this additive schema
    # before its older Alembic lineage catches up. Validate any existing objects.
    inspector = None if op.get_context().as_sql else sa.inspect(op.get_bind())
    existing = set() if inspector is None else set(inspector.get_table_names())
    article_column = None if inspector is None else next(
        (column for column in inspector.get_columns("article") if column["name"] == "channel_push_trusted"), None,
    )
    if article_column is None:
        op.add_column("article", sa.Column("channel_push_trusted", sa.Boolean, nullable=False, server_default="0"))
    elif (article_column["nullable"] or str(article_column["default"]).strip("()'\"") != "0"
          or not isinstance(article_column["type"], (sa.Boolean, mysql.TINYINT))):
        raise ValueError("Incompatible channel push trust column")

    def create_table(name, *elements):
        if name not in existing:
            op.create_table(name, *elements)
            return
        table = sa.Table(name, sa.MetaData(), *elements)
        columns = list(table.columns)
        actual = {column["name"]: column for column in inspector.get_columns(name)}
        if set(actual) != {column.name for column in columns}:
            raise ValueError(f"Incompatible channel table columns: {name}")
        if any(actual[column.name]["nullable"] != column.nullable for column in columns):
            raise ValueError(f"Incompatible channel table nullability: {name}")
        primary = inspector.get_pk_constraint(name)["constrained_columns"]
        if primary != [column.name for column in columns if column.primary_key]:
            raise ValueError(f"Incompatible channel table primary key: {name}")
        foreign_keys = {
            (tuple(key["constrained_columns"]), key["referred_table"], tuple(key["referred_columns"]),
             key["options"].get("ondelete")) for key in inspector.get_foreign_keys(name)
        }
        expected_keys = set()
        for column in columns:
            for key in column.foreign_keys:
                target_table, target_column = key.target_fullname.split(".")
                expected_keys.add(((column.name,), target_table, (target_column,), key.ondelete))
        if foreign_keys != expected_keys:
            raise ValueError(f"Incompatible channel table foreign keys: {name}")
        actual_unique = {
            (constraint["name"], tuple(constraint["column_names"]))
            for constraint in inspector.get_unique_constraints(name)
        }
        expected_unique = {
            (constraint.name, tuple(constraint.columns.keys()))
            for constraint in table.constraints if isinstance(constraint, sa.UniqueConstraint)
        }
        if not expected_unique.issubset(actual_unique):
            raise ValueError(f"Incompatible channel table unique constraints: {name}")

    create_table(
        "channel_push_config", channel_key(primary_key=True),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("description", sa.Text, nullable=False),
        sa.Column("enabled", sa.Boolean, nullable=False),
        sa.Column("schedule", sa.String(5), nullable=False),
        sa.Column("max_items", sa.Integer, nullable=False),
        sa.Column("source_type", sa.String(20), nullable=False),
        sa.Column("source_url", sa.Text),
        sa.Column("selector", sa.String(300)),
        sa.Column("include_terms", sa.JSON, nullable=False),
        sa.Column("exclude_terms", sa.JSON, nullable=False),
        sa.Column("updated_at", datetime6, nullable=False),
    )
    create_table(
        "channel_subscription", channel_key(primary_key=True), user_key(primary_key=True),
        sa.Column("binding_id", sa.Integer, nullable=False),
        sa.Column("binding_fingerprint", sa.String(64), nullable=False),
        sa.Column("created_at", datetime6, nullable=False),
    )
    create_table(
        "channel_push_delivery", sa.Column("id", sa.Integer, primary_key=True), channel_key(), user_key(),
        sa.Column("day", sa.Date, nullable=False),
        sa.Column("created_at", datetime6, nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("finished_at", datetime6),
        sa.UniqueConstraint("channel_id", "user_id", "day", name="uq_channel_push_delivery_day"),
    )
    create_table(
        "channel_website_snapshot", channel_key(primary_key=True), sa.Column("day", sa.Date, primary_key=True),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("source_fingerprint", sa.String(64), nullable=False),
        sa.Column("updated_at", datetime6, nullable=False),
        sa.Column("config_updated_at", datetime6, nullable=False),
        sa.Column("changed", sa.Boolean, nullable=False),
        sa.Column("items", sa.JSON, nullable=False),
    )


def downgrade():
    op.drop_table("channel_website_snapshot")
    op.drop_table("channel_push_delivery")
    op.drop_table("channel_subscription")
    op.drop_table("channel_push_config")
    op.drop_column("article", "channel_push_trusted")
