"""Schedule durable device webhook retries after failed deliveries.

Revision ID: c7e90b84a126
Revises: b9af0263d1e8
"""

from alembic import op
import sqlalchemy as sa

revision = "c7e90b84a126"
down_revision = "b9af0263d1e8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "subscription_device_events",
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
    )
    # Only due rows need index lookups; old failed rows with NULL are
    # deliberately eligible immediately after this forward-only migration.
    op.create_index(
        "ix_subscription_device_events_retry_due",
        "subscription_device_events",
        ["status", "next_attempt_at"],
    )
    # Legacy rows which had already reached the cap require manual recovery.
    op.execute(
        "UPDATE subscription_device_events SET status='exhausted' "
        "WHERE attempts >= 10 AND status IN ('failed','pending')"
    )


def downgrade() -> None:
    op.drop_index("ix_subscription_device_events_retry_due", table_name="subscription_device_events")
    op.drop_column("subscription_device_events", "next_attempt_at")
