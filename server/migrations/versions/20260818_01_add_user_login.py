"""add personal user login

Revision ID: 20260818_01
Revises: 20260811_01
Create Date: 2026-08-18
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql


revision: str = "20260818_01"
down_revision: Union[str, None] = "20260811_01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

datetime6 = sa.DateTime().with_variant(mysql.DATETIME(fsp=6), "mysql")


def upgrade() -> None:
    op.create_table(
        "user_account",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("username", sa.String(80), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("staff_id", sa.Integer(), sa.ForeignKey("staff.id"), nullable=False),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("last_login_at", datetime6, nullable=True),
        sa.Column("created_at", datetime6, nullable=False),
        sa.Column("updated_at", datetime6, nullable=False),
        sa.UniqueConstraint("username", name="uq_user_account_username"),
        sa.UniqueConstraint("staff_id", name="uq_user_account_staff_id"),
    )
    op.create_index("ix_user_account_username", "user_account", ["username"])
    op.create_index("ix_user_account_staff_id", "user_account", ["staff_id"])
    op.create_table(
        "user_session",
        sa.Column("token_hash", sa.String(64), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("user_account.id"), nullable=False),
        sa.Column("expires_at", datetime6, nullable=False),
        sa.Column("last_used_at", datetime6, nullable=False),
        sa.Column("created_at", datetime6, nullable=False),
    )
    op.create_index("ix_user_session_user_id", "user_session", ["user_id"])
    op.create_index("ix_user_session_expires_at", "user_session", ["expires_at"])


def downgrade() -> None:
    op.drop_index("ix_user_session_expires_at", table_name="user_session")
    op.drop_index("ix_user_session_user_id", table_name="user_session")
    op.drop_table("user_session")
    op.drop_index("ix_user_account_staff_id", table_name="user_account")
    op.drop_index("ix_user_account_username", table_name="user_account")
    op.drop_table("user_account")
