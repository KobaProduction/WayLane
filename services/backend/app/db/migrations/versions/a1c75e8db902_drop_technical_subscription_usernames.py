"""Remove stored product usernames; Subscription.uuid is the sole product identity.

Revision ID: a1c75e8db902
Revises: 7d644af4d4d3

Preserves existing UUID, internal node protocol row keys, usage, devices and
other foreign key references. Usernames are no longer generated or stored.
"""

from alembic import op

revision = "a1c75e8db902"
down_revision = "7d644af4d4d3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("subscriptions") as batch:
        batch.drop_index("ix_subscriptions_username")
        batch.drop_column("username")
    with op.batch_alter_table("subscription_templates") as batch:
        batch.drop_column("username_prefix")
        batch.drop_column("username_suffix")


def downgrade() -> None:
    # The removed product names must never be recreated by an automatic rollback.
    # This is an intentional breaking change in a development-only environment.
    raise RuntimeError("This UUID-first subscription schema migration is irreversible")
