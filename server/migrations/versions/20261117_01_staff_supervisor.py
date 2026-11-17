"""Add the optional direct supervisor of each staff member."""

import sqlalchemy as sa
from alembic import op

revision = "20261117_01"
down_revision = "20261112_03"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("staff") as batch:
        batch.add_column(sa.Column("supervisor_id", sa.Integer(), nullable=True))
        batch.create_index("ix_staff_supervisor_id", ["supervisor_id"])
        batch.create_foreign_key("fk_staff_supervisor", "staff", ["supervisor_id"], ["id"], ondelete="SET NULL")


def downgrade():
    with op.batch_alter_table("staff") as batch:
        batch.drop_constraint("fk_staff_supervisor", type_="foreignkey")
        batch.drop_index("ix_staff_supervisor_id")
        batch.drop_column("supervisor_id")
