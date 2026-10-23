"""Opt-in notifications, durable unknown outcomes and actual worker heartbeats."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.mysql import DATETIME

revision = "20261008_09"
down_revision = "20261008_08"
branch_labels = None
depends_on = None


def tables():
    m = sa.MetaData(); date = sa.DateTime().with_variant(DATETIME(fsp=6), "mysql")
    sa.Table("user_account", m, sa.Column("id", sa.Integer, primary_key=True))
    sa.Table("staff_channel", m, sa.Column("id", sa.Integer, primary_key=True))
    prefs = sa.Table("knowledge_preference", m,
        sa.Column("owner_id", sa.Integer, sa.ForeignKey("user_account.id"), primary_key=True),
        sa.Column("revision", sa.Integer, nullable=False), sa.Column("desktop_notifications", sa.Boolean, nullable=False),
        sa.Column("hermes_notifications", sa.Boolean, nullable=False), sa.Column("admin_alerts", sa.Boolean, nullable=False),
        sa.Column("channel_id", sa.Integer, sa.ForeignKey("staff_channel.id"), nullable=True))
    notifications = sa.Table("knowledge_notification", m,
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("owner_id", sa.Integer, sa.ForeignKey("user_account.id"), nullable=False, index=True),
        sa.Column("item_id", sa.String(40), nullable=True), sa.Column("source_revision", sa.Integer, nullable=False),
        sa.Column("kind", sa.String(40), nullable=False), sa.Column("channel_id", sa.Integer, nullable=True),
        sa.Column("recipient_hash", sa.String(64), nullable=False), sa.Column("delivery_id", sa.String(64), nullable=False),
        sa.Column("state", sa.String(30), nullable=False, index=True), sa.Column("attempts", sa.Integer, nullable=False),
        sa.Column("available_at", date, nullable=False), sa.Column("lease_until", date, nullable=True),
        sa.Column("read_at", date, nullable=True), sa.Column("created_at", date, nullable=False, index=True))
    heartbeat = sa.Table("knowledge_worker_heartbeat", m, sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("role", sa.String(20), nullable=False), sa.Column("state", sa.String(20), nullable=False),
        sa.Column("last_seen", date, nullable=False, index=True))
    alert = sa.Table("knowledge_operational_alert", m, sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("kind", sa.String(40), nullable=False), sa.Column("active_key", sa.String(40), nullable=True, unique=True),
        sa.Column("metrics", sa.JSON, nullable=False), sa.Column("created_at", date, nullable=False, index=True),
        sa.Column("resolved_at", date, nullable=True))
    return prefs, notifications, heartbeat, alert


def upgrade():
    for table in tables(): table.create(op.get_bind(), checkfirst=True)


def downgrade():
    values = tables()
    if any(op.get_bind().scalar(sa.select(sa.func.count()).select_from(table)) for table in values[:2]):
        raise RuntimeError("Preserve notification preferences and delivery outcomes")
    for table in reversed(values): table.drop(op.get_bind(), checkfirst=True)
