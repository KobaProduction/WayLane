"""Explicit job owners for the UUID-first WayLane Backend.

The legacy PasarGuard NodeService jobs (Node health probes, bridge heartbeat,
node reset and reset-on-read usage RPC) are intentionally not loaded.
Autonomous Nodes report health/usage through authenticated binary WSS and
persisted Node Registry instead. Other product and notification jobs retain
their established scheduler entrypoints and configuration gates.
"""

from importlib import import_module

_SCHEDULED_MODULES = (
    "cleanup_subscription_updates",
    "device_webhook_retry",
    "remove_expired_subscriptions",
    "reset_subscription_data_usage",
    "review_admins",
    "review_subscriptions",
    "send_notifications",
)

for _module in _SCHEDULED_MODULES:
    import_module(f"{__name__}.{_module}")
