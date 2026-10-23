"""Explicit version-bound team sharing; no default grants or personal ACL change."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.mysql import DATETIME

revision = "20261008_07"
down_revision = "20261008_06"
branch_labels = None
depends_on = None


def tables():
    m = sa.MetaData(); date = sa.DateTime().with_variant(DATETIME(fsp=6), "mysql")
    sa.Table("user_account", m, sa.Column("id", sa.Integer, primary_key=True))
    sa.Table("knowledge_item", m, sa.Column("id", sa.String(40), primary_key=True))
    team = sa.Table("knowledge_team", m,
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("owner_id", sa.Integer, sa.ForeignKey("user_account.id"), nullable=False, index=True),
        sa.Column("name", sa.String(160), nullable=False), sa.Column("revision", sa.Integer, nullable=False),
        sa.Column("enabled", sa.Boolean, nullable=False), sa.Column("created_at", date, nullable=False))
    member = sa.Table("knowledge_team_member", m,
        sa.Column("team_id", sa.String(40), sa.ForeignKey("knowledge_team.id"), primary_key=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("user_account.id"), primary_key=True),
        sa.Column("role", sa.String(20), nullable=False), sa.Column("created_at", date, nullable=False))
    share = sa.Table("knowledge_share", m,
        sa.Column("team_id", sa.String(40), sa.ForeignKey("knowledge_team.id"), primary_key=True),
        sa.Column("item_id", sa.String(40), sa.ForeignKey("knowledge_item.id"), primary_key=True),
        sa.Column("source_revision", sa.Integer, nullable=False),
        sa.Column("shared_by", sa.Integer, sa.ForeignKey("user_account.id"), nullable=False),
        sa.Column("created_at", date, nullable=False))
    event = sa.Table("knowledge_team_event", m,
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("team_id", sa.String(40), sa.ForeignKey("knowledge_team.id"), nullable=False, index=True),
        sa.Column("actor_id", sa.Integer, sa.ForeignKey("user_account.id"), nullable=False),
        sa.Column("action", sa.String(30), nullable=False), sa.Column("metadata_json", sa.JSON, nullable=False),
        sa.Column("created_at", date, nullable=False))
    return team, member, share, event


def upgrade():
    for table in tables(): table.create(op.get_bind(), checkfirst=True)


def downgrade():
    values = tables()
    if any(op.get_bind().scalar(sa.select(sa.func.count()).select_from(table)) for table in values):
        raise RuntimeError("Team membership, grants and audit history must be preserved during rollback")
    for table in reversed(values): table.drop(op.get_bind(), checkfirst=True)
