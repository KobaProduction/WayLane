"""Merge the autonomous Node AWG branch with UUID-only subscriptions.

Revision ID: f0556e841d8b
Revises: a7e36d1b4f90, d9f2e8714c33

Both branches own independent tables/columns. Preserve their revisions
instead of rewriting applied history or migrating live identifiers.
"""

revision = "f0556e841d8b"
down_revision = ("a7e36d1b4f90", "d9f2e8714c33")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    raise RuntimeError("The UUID-only migration history is intentionally irreversible")
