"""Provider-only notification publisher: webhook events, never chat-bot delivery."""

import asyncio
from functools import wraps

from app.models.admin import AdminDetails
from app.models.subscription_models import SubscriptionNotificationResponse
from app.settings import notification_enable
from app.utils.logger import get_logger

from . import webhook as wh
from .dedup import claim_notification_slot

logger = get_logger("Notification")


def _safe_notification_task(func):
    @wraps(func)
    async def wrapper(*args, **kwargs):
        try:
            if not await claim_notification_slot(func.__name__, args, kwargs):
                return None
            return await func(*args, **kwargs)
        except asyncio.CancelledError:
            raise
        except Exception as err:
            logger.warning(
                "Webhook notification %s failed: %s",
                func.__name__,
                type(err).__name__,
                exc_info=(type(err), err, err.__traceback__),
            )

    return wrapper


async def _emit(action: str, resource: str, resource_id=None, actor: str | None = None):
    await wh.notify(wh.PanelEvent(action=action, resource=resource, resource_id=resource_id, actor=actor))


async def create_api_key(api_key, admin_username, by):
    if (await notification_enable()).api_key.create:
        await _emit("api_key.create", "api_key", getattr(api_key, "id", None), by)


async def modify_api_key(api_key, admin_username, by):
    if (await notification_enable()).api_key.modify:
        await _emit("api_key.modify", "api_key", getattr(api_key, "id", None), by)


async def remove_api_key(api_key, admin_username, by):
    if (await notification_enable()).api_key.delete:
        await _emit("api_key.delete", "api_key", getattr(api_key, "id", None), by)


async def create_admin_role(role, by):
    if (await notification_enable()).admin_role.create:
        await _emit("admin_role.create", "admin_role", role.id, by)


async def modify_admin_role(role, by):
    if (await notification_enable()).admin_role.modify:
        await _emit("admin_role.modify", "admin_role", role.id, by)


async def remove_admin_role(role, by):
    if (await notification_enable()).admin_role.delete:
        await _emit("admin_role.delete", "admin_role", role.id, by)


async def create_host(host, by):
    if (await notification_enable()).host.create:
        await _emit("host.create", "host", getattr(host, "id", None), by)


async def modify_host(host, by):
    if (await notification_enable()).host.modify:
        await _emit("host.modify", "host", getattr(host, "id", None), by)


async def remove_host(host, by):
    if (await notification_enable()).host.delete:
        await _emit("host.delete", "host", getattr(host, "id", None), by)


async def modify_hosts(by):
    if (await notification_enable()).host.modify_hosts:
        await _emit("host.modify_hosts", "host", None, by)


async def create_subscription_template(user, by):
    if (await notification_enable()).subscription_template.create:
        await _emit("subscription_template.create", "subscription_template", getattr(user, "id", None), by)


async def modify_subscription_template(user, by):
    if (await notification_enable()).subscription_template.modify:
        await _emit("subscription_template.modify", "subscription_template", getattr(user, "id", None), by)


async def remove_subscription_template(name, by):
    if (await notification_enable()).subscription_template.delete:
        await _emit("subscription_template.delete", "subscription_template", name, by)


async def create_node(node, by):
    if (await notification_enable()).node.create:
        await _emit("node.create", "node", getattr(node, "id", None), by)


async def modify_node(node, by):
    if (await notification_enable()).node.modify:
        await _emit("node.modify", "node", getattr(node, "id", None), by)


async def remove_node(node, by):
    if (await notification_enable()).node.delete:
        await _emit("node.delete", "node", getattr(node, "id", None), by)


async def connect_node(node):
    if (await notification_enable()).node.connect:
        await _emit("node.connect", "node", getattr(node, "id", None), None)


async def recovered_node(node):
    if (await notification_enable()).node.recovered:
        await _emit("node.recovered", "node", getattr(node, "id", None), None)


async def error_node(node):
    if (await notification_enable()).node.error:
        await _emit("node.error", "node", getattr(node, "id", None), None)


async def limited_node(node, data_limit, used_traffic):
    if (await notification_enable()).node.limited:
        await _emit("node.limited", "node", getattr(node, "id", None), None)


async def reset_node_usage(node, by, uplink, downlink):
    if (await notification_enable()).node.reset_usage:
        await _emit("node.reset_usage", "node", getattr(node, "id", None), by)


async def create_group(group, by):
    if (await notification_enable()).group.create:
        await _emit("group.create", "group", getattr(group, "id", None), by)


async def modify_group(group, by):
    if (await notification_enable()).group.modify:
        await _emit("group.modify", "group", getattr(group, "id", None), by)


async def remove_group(group_id, by):
    if (await notification_enable()).group.delete:
        await _emit("group.delete", "group", group_id, by)


async def create_admin(admin, by):
    if (await notification_enable()).admin.create:
        await _emit("admin.create", "admin", getattr(admin, "id", None), by)


async def modify_admin(admin, by):
    if (await notification_enable()).admin.modify:
        await _emit("admin.modify", "admin", getattr(admin, "id", None), by)


async def remove_admin(username, by):
    if (await notification_enable()).admin.delete:
        await _emit("admin.delete", "admin", username, by)


async def admin_usage_reset(admin, by):
    if (await notification_enable()).admin.reset_usage:
        await _emit("admin.reset_usage", "admin", getattr(admin, "id", None), by)


async def admin_usage_limit_reached(admin, usage_percentage, threshold):
    if (await notification_enable()).admin.usage_limit_warning:
        await _emit("admin.usage_limit_warning", "admin", getattr(admin, "id", None), None)


async def admin_login(username, password, client_ip, success):
    if (await notification_enable()).admin.login:
        await _emit("admin.login", "admin", None, None)


async def create_core(core, by):
    if (await notification_enable()).core.create:
        await _emit("core.create", "core", getattr(core, "id", None), by)


async def modify_core(core, by):
    if (await notification_enable()).core.modify:
        await _emit("core.modify", "core", getattr(core, "id", None), by)


async def remove_core(core_id, by):
    if (await notification_enable()).core.delete:
        await _emit("core.delete", "core", core_id, by)


async def subscription_status_change(user: SubscriptionNotificationResponse, by: AdminDetails):
    if (await notification_enable()).subscription.status_change:
        await wh.status_change(user)


async def _emit_subscription(action: str, subscription: SubscriptionNotificationResponse, by: AdminDetails) -> None:
    await wh.notify(
        wh.PanelEvent(
            action=action,
            resource="subscription",
            subscription_uuid=subscription.uuid,
            actor=by.username,
        )
    )


async def create_subscription(user: SubscriptionNotificationResponse, by: AdminDetails):
    if (await notification_enable()).subscription.create:
        await _emit_subscription("subscription.create", user, by)


async def modify_subscription(user: SubscriptionNotificationResponse, by: AdminDetails):
    if (await notification_enable()).subscription.modify:
        await _emit_subscription("subscription.update", user, by)


async def remove_subscription(user: SubscriptionNotificationResponse, by: AdminDetails):
    if (await notification_enable()).subscription.delete:
        await _emit_subscription("subscription.delete", user, by)


async def reset_subscription_data_usage(user: SubscriptionNotificationResponse, by: AdminDetails):
    if (await notification_enable()).subscription.reset_data_usage:
        await _emit_subscription("subscription.traffic_reset", user, by)


async def subscription_data_reset_by_next(user: SubscriptionNotificationResponse, by: AdminDetails):
    if (await notification_enable()).subscription.data_reset_by_next:
        await _emit_subscription("subscription.next_plan_activated", user, by)


async def subscription_access_revoked(user: SubscriptionNotificationResponse, by: AdminDetails):
    if (await notification_enable()).subscription.subscription_revoked:
        await _emit_subscription("subscription.access_revoked", user, by)


create_api_key = _safe_notification_task(create_api_key)
modify_api_key = _safe_notification_task(modify_api_key)
remove_api_key = _safe_notification_task(remove_api_key)
create_admin_role = _safe_notification_task(create_admin_role)
modify_admin_role = _safe_notification_task(modify_admin_role)
remove_admin_role = _safe_notification_task(remove_admin_role)
create_host = _safe_notification_task(create_host)
modify_host = _safe_notification_task(modify_host)
remove_host = _safe_notification_task(remove_host)
modify_hosts = _safe_notification_task(modify_hosts)
create_subscription_template = _safe_notification_task(create_subscription_template)
modify_subscription_template = _safe_notification_task(modify_subscription_template)
remove_subscription_template = _safe_notification_task(remove_subscription_template)
create_node = _safe_notification_task(create_node)
modify_node = _safe_notification_task(modify_node)
remove_node = _safe_notification_task(remove_node)
connect_node = _safe_notification_task(connect_node)
recovered_node = _safe_notification_task(recovered_node)
error_node = _safe_notification_task(error_node)
limited_node = _safe_notification_task(limited_node)
reset_node_usage = _safe_notification_task(reset_node_usage)
create_group = _safe_notification_task(create_group)
modify_group = _safe_notification_task(modify_group)
remove_group = _safe_notification_task(remove_group)
create_admin = _safe_notification_task(create_admin)
modify_admin = _safe_notification_task(modify_admin)
remove_admin = _safe_notification_task(remove_admin)
admin_usage_reset = _safe_notification_task(admin_usage_reset)
admin_usage_limit_reached = _safe_notification_task(admin_usage_limit_reached)
admin_login = _safe_notification_task(admin_login)
create_core = _safe_notification_task(create_core)
modify_core = _safe_notification_task(modify_core)
remove_core = _safe_notification_task(remove_core)
subscription_status_change = _safe_notification_task(subscription_status_change)
create_subscription = _safe_notification_task(create_subscription)
modify_subscription = _safe_notification_task(modify_subscription)
remove_subscription = _safe_notification_task(remove_subscription)
reset_subscription_data_usage = _safe_notification_task(reset_subscription_data_usage)
subscription_data_reset_by_next = _safe_notification_task(subscription_data_reset_by_next)
subscription_access_revoked = _safe_notification_task(subscription_access_revoked)
