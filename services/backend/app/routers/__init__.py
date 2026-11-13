from fastapi import APIRouter

from . import (
    acme,
    admin,
    admin_role,
    api_key,
    browser_telemetry,
    business_analytics,
    client_template,
    core,
    core_presets,
    group,
    home,
    host,
    host_balancer,
    node_control_ws,
    node_registry,
    passkey,
    settings,
    setup,
    subscription,
    subscription_firewall,
    subscription_template,
    subscriptions,
    system,
    tls_presets,
)

api_router = APIRouter()

routers = [
    home.router,
    admin.router,
    acme.router,
    api_key.router,
    admin_role.router,
    setup.router,
    system.router,
    settings.router,
    tls_presets.router,
    group.router,
    core.router,
    core_presets.router,
    business_analytics.router,
    browser_telemetry.router,
    client_template.router,
    host.router,
    host_balancer.router,
    node_registry.node_registry_router,
    node_registry.node_control_router,
    node_control_ws.router,
    subscription.router,
    subscription_firewall.router,
    subscriptions.router,
    subscription_template.router,
    passkey.router,
]

for router in routers:
    api_router.include_router(router)

__all__ = ["api_router"]
