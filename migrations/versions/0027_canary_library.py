"""Add the deterministic canary library.

Revision ID: 0027
Revises: 0026
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0027"
down_revision: str | Sequence[str] | None = "0026"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_TYPES = ("secret_leak", "forbidden_tool")
NEW_TYPES = (
    "system_prompt_leak",
    "unauthorized_action",
    "data_exfiltration",
    "phishing_link_injection",
    "malicious_code_injection",
    "policy_violation",
)

NEW_CHECK = (
    "(type IN ('secret_leak', 'system_prompt_leak', 'phishing_link_injection',"
    " 'malicious_code_injection') AND secret_enc IS NOT NULL AND rule_json IS NULL)"
    " OR (type IN ('forbidden_tool', 'unauthorized_action', 'policy_violation')"
    " AND rule_json IS NOT NULL AND secret_enc IS NULL)"
    " OR (type = 'data_exfiltration' AND secret_enc IS NOT NULL AND rule_json IS NOT NULL)"
)
OLD_CHECK = (
    "(type = 'secret_leak' AND secret_enc IS NOT NULL AND rule_json IS NULL)"
    " OR (type = 'forbidden_tool' AND rule_json IS NOT NULL AND secret_enc IS NULL)"
)


def upgrade() -> None:
    for value in NEW_TYPES:
        with op.get_context().autocommit_block():
            op.execute(f"ALTER TYPE canary_type ADD VALUE IF NOT EXISTS '{value}'")
    op.drop_constraint(op.f("ck_canaries_definition_matches_type"), "canaries", type_="check")
    op.create_check_constraint("definition_matches_type", "canaries", NEW_CHECK)


def downgrade() -> None:
    op.execute(
        "DELETE FROM findings WHERE canary_id IN"
        " (SELECT id FROM canaries WHERE type::text NOT IN ('secret_leak', 'forbidden_tool'))"
    )
    op.execute("DELETE FROM canaries WHERE type::text NOT IN ('secret_leak', 'forbidden_tool')")
    op.drop_constraint(op.f("ck_canaries_definition_matches_type"), "canaries", type_="check")
    op.execute("ALTER TYPE canary_type RENAME TO canary_type_with_library")
    op.execute("CREATE TYPE canary_type AS ENUM ('secret_leak', 'forbidden_tool')")
    op.execute(
        "ALTER TABLE canaries ALTER COLUMN type TYPE canary_type USING type::text::canary_type"
    )
    op.execute("DROP TYPE canary_type_with_library")
    op.create_check_constraint("definition_matches_type", "canaries", OLD_CHECK)
