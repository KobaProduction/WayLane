"""WayLane schema compatibility bridge for historical PasarGuard databases.

Revision ID: 21bec95874f4
Revises: c8d2e4f6a1b3

Existing PasarGuard installations have already created and populated the
application tables. Do not bootstrap or overwrite their settings, users,
credentials, JWT signing keys, or node records.
"""

from alembic import op
from sqlalchemy.dialects import postgresql

revision = "21bec95874f4"
down_revision = "c8d2e4f6a1b3"
branch_labels = None
depends_on = None

# The legacy schema and current WayLane metadata share all tables, columns
# and indexes. These three types differ on PostgreSQL; old JSON values can
# be converted to JSONB, without replacing or resetting any rows.
_JSONB_COLUMNS = (
    ("admin_roles", "hwid"),
    ("admins", "custom_variables"),
    ("api_keys", "permissions"),
)


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    for table, column in _JSONB_COLUMNS:
        op.alter_column(
            table, column,
            existing_type=postgresql.JSON(),
            type_=postgresql.JSONB(),
            postgresql_using=f"{column}::jsonb",
        )


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    for table, column in reversed(_JSONB_COLUMNS):
        op.alter_column(
            table, column,
            existing_type=postgresql.JSONB(),
            type_=postgresql.JSON(),
            postgresql_using=f"{column}::json",
        )
