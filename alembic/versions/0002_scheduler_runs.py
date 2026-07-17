"""add scheduler run locks

Revision ID: 0002_scheduler_runs
Revises: 0001_initial
Create Date: 2026-07-18
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0002_scheduler_runs"
down_revision: str | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "scheduler_runs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("job_name", sa.String(length=120), nullable=False),
        sa.Column("run_key", sa.String(length=120), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("result", sa.JSON(), nullable=False),
        sa.UniqueConstraint("job_name", "run_key", name="uq_scheduler_job_run"),
    )
    op.create_index("ix_scheduler_runs_job_name", "scheduler_runs", ["job_name"])
    op.create_index("ix_scheduler_runs_run_key", "scheduler_runs", ["run_key"])
    op.create_index("ix_scheduler_runs_status", "scheduler_runs", ["status"])


def downgrade() -> None:
    op.drop_table("scheduler_runs")
