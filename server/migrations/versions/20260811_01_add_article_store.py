"""add shared article store and per-staff state

Revision ID: 20260811_01
Revises:
Create Date: 2026-08-11
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql


revision: str = "20260811_01"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

long_text = sa.Text().with_variant(mysql.LONGTEXT(), "mysql")
datetime6 = sa.DateTime().with_variant(mysql.DATETIME(fsp=6), "mysql")


def upgrade() -> None:
    op.create_table(
        "smart_channel",
        sa.Column("id", sa.String(80), primary_key=True),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("prompt", long_text, nullable=False),
        sa.Column("topic", sa.String(200), nullable=False),
        sa.Column("server", sa.String(500), nullable=False),
        sa.Column("schedule", sa.String(50), nullable=False),
        sa.Column("max_items", sa.Integer(), nullable=False),
        sa.Column("include_terms", sa.JSON(), nullable=False),
        sa.Column("exclude_terms", sa.JSON(), nullable=False),
        sa.Column("color", sa.String(20), nullable=False),
        sa.Column("state", sa.String(20), nullable=False),
        sa.Column("created_at", datetime6, nullable=False),
        sa.Column("updated_at", datetime6, nullable=False),
    )
    op.create_table(
        "article",
        sa.Column("id", sa.String(191), primary_key=True),
        sa.Column("channel_id", sa.String(80), nullable=False),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("body", long_text, nullable=False),
        sa.Column("source", sa.String(240), nullable=False),
        sa.Column("click_url", sa.Text(), nullable=True),
        sa.Column("tags", sa.JSON(), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("published_at", datetime6, nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("created_at", datetime6, nullable=False),
        sa.Column("updated_at", datetime6, nullable=False),
    )
    op.create_index("ix_article_channel_id", "article", ["channel_id"])
    op.create_index("ix_article_published_at", "article", ["published_at"])
    op.create_index("ix_article_content_hash", "article", ["content_hash"])
    op.create_table(
        "article_user_state",
        sa.Column("operator_name", sa.String(80), primary_key=True),
        sa.Column("article_id", sa.String(191), sa.ForeignKey("article.id"), primary_key=True),
        sa.Column("read", sa.Boolean(), nullable=False),
        sa.Column("saved", sa.Boolean(), nullable=False),
        sa.Column("feedback", sa.String(20), nullable=True),
        sa.Column("deleted_at", datetime6, nullable=True),
        sa.Column("purge_at", datetime6, nullable=True),
        sa.Column("permanently_deleted_at", datetime6, nullable=True),
        sa.Column("state_updated_at_ms", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", datetime6, nullable=False),
    )
    op.create_index(
        "ix_article_user_state_trash",
        "article_user_state",
        ["operator_name", "deleted_at", "permanently_deleted_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_article_user_state_trash", table_name="article_user_state")
    op.drop_table("article_user_state")
    op.drop_index("ix_article_content_hash", table_name="article")
    op.drop_index("ix_article_published_at", table_name="article")
    op.drop_index("ix_article_channel_id", table_name="article")
    op.drop_table("article")
    op.drop_table("smart_channel")
