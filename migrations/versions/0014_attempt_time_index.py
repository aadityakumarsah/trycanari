"""An index for the platform-wide token budget.

Revision ID: 0014
Revises: 0013

Every attack asks how many tokens all replays have used in the last 24 hours.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0014"
down_revision: str | Sequence[str] | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index("ix_attempts_submitted_at", "attempts", ["submitted_at"])


def downgrade() -> None:
    op.drop_index("ix_attempts_submitted_at", "attempts")
