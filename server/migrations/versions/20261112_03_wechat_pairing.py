"""Allow a verified Hermes private conversation to bind a member's WeChat."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.mysql import DATETIME

revision = "20261112_03"
down_revision = "20261112_02"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("staff_channel", sa.Column("wechat_profile", sa.String(64), nullable=True))
    timestamp = sa.DateTime().with_variant(DATETIME(fsp=6), "mysql", "mariadb")
    op.create_table(
        "wechat_pairing",
        sa.Column("staff_id", sa.Integer(), sa.ForeignKey("staff.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("id", sa.String(32), nullable=False, unique=True),
        sa.Column("requested_by", sa.Integer(), sa.ForeignKey("user_account.id", ondelete="CASCADE"), nullable=False),
        sa.Column("code_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("previous_binding_hash", sa.String(64), nullable=False),
        sa.Column("expires_at", timestamp, nullable=False),
        sa.Column("recipient_id", sa.String(160)),
        sa.Column("profile", sa.String(64)),
        sa.Column("confirmed_at", timestamp),
    )


def downgrade():
    op.drop_table("wechat_pairing")
    op.drop_column("staff_channel", "wechat_profile")
