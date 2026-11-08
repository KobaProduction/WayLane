from sqlalchemy.ext.asyncio import AsyncSession

from .base import Base, GetDB, get_db
from .models import JWT, Subscription, System

__all__ = [
    "JWT",
    "AsyncSession",
    "Base",
    "GetDB",
    "Subscription",
    "System",
    "get_db",
]

# Register TLS preset tables in the central SQLAlchemy metadata.
# Register managed ACME accounts and orders with the central metadata.
# Client-side Xray subscription balancer groups.
# Persist client notice design and device webhook outbox.
# Managed Xray core security and routing presets.
from app.db import (
    acme as _acme_tables,  # noqa: F401
    core_presets as _core_presets_tables,  # noqa: F401
    host_balancer as _host_balancer_tables,  # noqa: F401
    subscription_firewall as _subscription_firewall_tables,  # noqa: F401
    tls as _tls_tables,  # noqa: F401
)
