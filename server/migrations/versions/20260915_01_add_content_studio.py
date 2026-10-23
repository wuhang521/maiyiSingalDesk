"""新增账号配置与分步创作档案。"""
from alembic import op
import sqlalchemy as sa

revision = "20260915_01"
down_revision = "20260818_01"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("content_account",
                    sa.Column("id", sa.String(40), primary_key=True),
                    sa.Column("owner_id", sa.Integer(), sa.ForeignKey("user_account.id"), nullable=False),
                    sa.Column("revision", sa.Integer(), nullable=False),
                    sa.Column("document", sa.JSON(), nullable=False))
    op.create_index("ix_content_account_owner_id", "content_account", ["owner_id"])
    op.create_table("content_episode",
                    sa.Column("id", sa.String(40), primary_key=True),
                    sa.Column("account_id", sa.String(40), sa.ForeignKey("content_account.id"), nullable=False),
                    sa.Column("revision", sa.Integer(), nullable=False),
                    sa.Column("document", sa.JSON(), nullable=False))
    op.create_index("ix_content_episode_account_id", "content_episode", ["account_id"])


def downgrade():
    op.drop_table("content_episode")
    op.drop_table("content_account")
