"""Owner-scoped video projects, versioned reviews and idempotent jobs."""
from alembic import op
import sqlalchemy as sa

revision = "20261019_01"
down_revision = "20261008_12"
branch_labels = None
depends_on = None


def tables():
    metadata = sa.MetaData()
    sa.Table("user_account", metadata, sa.Column("id", sa.Integer, primary_key=True))
    projects = sa.Table("video_project", metadata,
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("owner_id", sa.Integer, sa.ForeignKey("user_account.id"), nullable=False, index=True),
        sa.Column("topic", sa.Text, nullable=False),
        sa.Column("mode", sa.String(20), nullable=False),
        sa.Column("platforms", sa.JSON, nullable=False),
        sa.Column("revision", sa.Integer, nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("plan", sa.JSON, nullable=False),
        sa.Column("locked_fields", sa.JSON, nullable=False),
        sa.Column("created_at", sa.DateTime, nullable=False),
        sa.Column("updated_at", sa.DateTime, nullable=False))
    reviews = sa.Table("video_project_review", metadata,
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(36), sa.ForeignKey("video_project.id"), nullable=False, index=True),
        sa.Column("revision", sa.Integer, nullable=False),
        sa.Column("stage", sa.String(20), nullable=False),
        sa.Column("decision", sa.String(20), nullable=False),
        sa.Column("quote", sa.Text, nullable=False),
        sa.Column("confirmed_at", sa.DateTime, nullable=False),
        sa.UniqueConstraint("project_id", "revision", "stage", name="uq_video_review_stage"))
    jobs = sa.Table("video_project_job", metadata,
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(36), sa.ForeignKey("video_project.id"), nullable=False, index=True),
        sa.Column("revision", sa.Integer, nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("scene_id", sa.String(100), nullable=True),
        sa.Column("idempotency_key", sa.String(160), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(30), nullable=False, index=True),
        sa.Column("provider_task_id", sa.String(500), nullable=True),
        sa.Column("result", sa.JSON, nullable=False),
        sa.Column("error", sa.Text, nullable=True),
        sa.Column("estimated_cost", sa.JSON, nullable=False),
        sa.Column("budget_grant", sa.JSON, nullable=False),
        sa.Column("snapshot", sa.JSON, nullable=False),
        sa.Column("lease_token", sa.String(100), nullable=True),
        sa.Column("created_at", sa.DateTime, nullable=False),
        sa.Column("updated_at", sa.DateTime, nullable=False),
        sa.UniqueConstraint("project_id", "idempotency_key", name="uq_video_job_idempotency"))
    return projects, reviews, jobs


def upgrade():
    for table in tables():
        table.create(op.get_bind(), checkfirst=True)


def downgrade():
    values = tables()
    for table in reversed(values):
        if sa.inspect(op.get_bind()).has_table(table.name):
            if op.get_bind().scalar(sa.select(sa.func.count()).select_from(table)):
                raise RuntimeError("视频生产记录非空，请先备份迁移，不能自动删除确认及付费回执")
    for table in reversed(values):
        table.drop(op.get_bind(), checkfirst=True)
