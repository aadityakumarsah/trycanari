"""Design-partner pilot applications and funnel stages.

Revision ID: 0030
Revises: 0029
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0030"
down_revision: str | Sequence[str] | None = "0029"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "pilot_applications",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("work_email", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("company", sa.Text(), nullable=False),
        sa.Column("company_website", sa.Text(), nullable=True),
        sa.Column("role", sa.Text(), nullable=True),
        sa.Column("stage", sa.Text(), nullable=False),
        sa.Column("use_case", sa.Text(), nullable=False),
        sa.Column("goals_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("source", sa.Text(), server_default="direct", nullable=False),
        sa.Column("status", sa.Text(), server_default="new", nullable=False),
        sa.Column("notes", sa.Text(), server_default="", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("stage IN ('prototype', 'beta', 'production')", name="stage_known"),
        sa.CheckConstraint(
            "status IN ('new', 'contacted', 'qualified', 'pilot', 'paid', 'closed')",
            name="status_known",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("work_email"),
    )
    op.create_index("ix_pilot_applications_created_at", "pilot_applications", ["created_at"])
    op.create_index("ix_pilot_applications_status", "pilot_applications", ["status"])
    op.execute('ALTER TABLE "pilot_applications" ENABLE ROW LEVEL SECURITY')


def downgrade() -> None:
    op.drop_index("ix_pilot_applications_status", table_name="pilot_applications")
    op.drop_index("ix_pilot_applications_created_at", table_name="pilot_applications")
    op.drop_table("pilot_applications")
