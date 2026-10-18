"""Control workspaces, encrypted runs, and append-only audit events.

Revision ID: 0032
Revises: 0031
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0032"
down_revision: str | Sequence[str] | None = "0031"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "control_projects",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("sponsor_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("adapter_kind", sa.Text(), server_default="text", nullable=False),
        sa.Column("operations_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("adapter_kind IN ('text', 'activation')", name="adapter_kind_known"),
        sa.ForeignKeyConstraint(["sponsor_id"], ["sponsors.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_control_projects_sponsor_id", "control_projects", ["sponsor_id"])
    op.create_index(
        "ix_control_projects_sponsor_created",
        "control_projects",
        ["sponsor_id", "created_at"],
    )

    op.create_table(
        "control_runs",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("input_digest", sa.String(length=64), nullable=False),
        sa.Column("response_digest", sa.String(length=64), nullable=False),
        sa.Column("overall", sa.Text(), nullable=False),
        sa.Column("result_enc", sa.LargeBinary(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("overall IN ('pass', 'fail', 'unknown')", name="overall_known"),
        sa.ForeignKeyConstraint(["project_id"], ["control_projects.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_control_runs_project_id", "control_runs", ["project_id"])
    op.create_index(
        "ix_control_runs_project_created",
        "control_runs",
        ["project_id", "created_at"],
    )

    op.create_table(
        "control_events",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("detail_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["run_id"], ["control_runs.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_control_events_run_id", "control_events", ["run_id"])
    op.create_index("ix_control_events_run_created", "control_events", ["run_id", "created_at"])
    op.execute('ALTER TABLE "control_projects" ENABLE ROW LEVEL SECURITY')
    op.execute('ALTER TABLE "control_runs" ENABLE ROW LEVEL SECURITY')
    op.execute('ALTER TABLE "control_events" ENABLE ROW LEVEL SECURITY')
    op.execute(
        "CREATE FUNCTION reject_control_event_change() RETURNS trigger AS $$ "
        "BEGIN RAISE EXCEPTION 'control events are append-only'; END; $$ LANGUAGE plpgsql"
    )
    op.execute(
        "CREATE TRIGGER control_events_no_update BEFORE UPDATE OR DELETE ON control_events "
        "FOR EACH ROW EXECUTE FUNCTION reject_control_event_change()"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS control_events_no_update ON control_events")
    op.execute("DROP FUNCTION IF EXISTS reject_control_event_change()")
    op.drop_index("ix_control_events_run_created", table_name="control_events")
    op.drop_index("ix_control_events_run_id", table_name="control_events")
    op.drop_table("control_events")
    op.drop_index("ix_control_runs_project_created", table_name="control_runs")
    op.drop_index("ix_control_runs_project_id", table_name="control_runs")
    op.drop_table("control_runs")
    op.drop_index("ix_control_projects_sponsor_created", table_name="control_projects")
    op.drop_index("ix_control_projects_sponsor_id", table_name="control_projects")
    op.drop_table("control_projects")
