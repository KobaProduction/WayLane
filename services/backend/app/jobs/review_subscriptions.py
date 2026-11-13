import asyncio
from datetime import UTC, datetime as dt, timedelta as td

from sqlalchemy.ext.asyncio import AsyncSession

from app import notification, scheduler
from app.db import GetDB
from app.db.crud.subscription import (
    bulk_create_notification_reminders,
    get_active_to_expire_subscriptions,
    get_active_to_limited_subscriptions,
    get_days_left_reached_subscriptions,
    get_usage_percentage_reached_subscriptions,
    update_subscriptions_status,
)
from app.db.models import ReminderType, Subscription, SubscriptionStatus
from app.jobs.dependencies import SYSTEM_ADMIN
from app.models.settings import Webhook
from app.operation import OperatorType
from app.operation.subscription_management import SubscriptionManagementOperation
from app.settings import webhook_settings
from app.subscription_delivery import sync_subscriptions
from app.utils.logger import get_logger
from config import job_settings, runtime_settings

logger = get_logger("review-subscriptions")
user_operator = SubscriptionManagementOperation(operator_type=OperatorType.SYSTEM)


async def _notify_status_change(db_user: Subscription, status: SubscriptionStatus) -> None:
    user = await user_operator.validate_subscription(db_user)
    asyncio.create_task(notification.subscription_status_change(user, SYSTEM_ADMIN))
    logger.info(f'Subscription "{user.uuid}" status changed to {status.value}')


async def apply_status_changes(db: AsyncSession, subscriptions: list[Subscription], status: SubscriptionStatus) -> None:
    """Revoke expired/limited node access; commercial renewal belongs exclusively to VPN Core.

    The panel never activates a next plan on its own. A renewal must arrive
    through the scoped Core entitlement/policy API with its revision.
    """
    if not subscriptions:
        return
    if status in (SubscriptionStatus.expired, SubscriptionStatus.limited):
        await update_subscriptions_status(db, subscriptions, status)
    await sync_subscriptions(subscriptions)
    for subscription in subscriptions:
        await _notify_status_change(subscription, status)


async def expire_users_job():
    async with GetDB() as db:
        if expired_users := await get_active_to_expire_subscriptions(db):
            await apply_status_changes(db, expired_users, SubscriptionStatus.expired)


async def limit_users_job():
    async with GetDB() as db:
        if limited_users := await get_active_to_limited_subscriptions(db):
            await apply_status_changes(db, limited_users, SubscriptionStatus.limited)


async def usage_percent_notification_job():
    settings: Webhook = await webhook_settings()
    if not settings.enable:
        return
    async with GetDB() as db:
        for percent in settings.usage_percent:
            users = await get_usage_percentage_reached_subscriptions(db, percent)

            # Prepare webhook notifications first
            webhook_data = []
            reminder_data = []

            for user in users:
                usage_percentage = user.usage_percentage
                # Queue an isolated product event: no numeric ID, username or contact data.
                webhook_data.append(
                    notification.wh.PanelEvent(
                        action="subscription.usage_threshold",
                        resource="subscription",
                        subscription_uuid=user.uuid,
                        details={"used_percent": usage_percentage},
                    )
                )

                # Prepare reminder data for bulk insert
                reminder_data.append(
                    {
                        "subscription_uuid": user.uuid,
                        "type": ReminderType.data_usage,
                        "threshold": percent,
                        "expires_at": user.expire if user.expire else None,
                    }
                )

            # Bulk create notification reminders
            if reminder_data:
                await bulk_create_notification_reminders(db, reminder_data)

            if webhook_data:
                await notification.wh.bulk_notify(webhook_data)


async def days_left_notification_job():
    settings: Webhook = await webhook_settings()
    if not settings.enable:
        return
    async with GetDB() as db:
        for days in settings.days_left:
            users = await get_days_left_reached_subscriptions(db, days)

            # Prepare webhook notifications first
            webhook_data = []
            reminder_data = []

            for user in users:
                days_left = user.days_left
                webhook_data.append(
                    notification.wh.PanelEvent(
                        action="subscription.expiry_threshold",
                        resource="subscription",
                        subscription_uuid=user.uuid,
                        details={"days_left": days_left},
                    )
                )

                # Prepare reminder data for bulk insert
                reminder_data.append(
                    {
                        "subscription_uuid": user.uuid,
                        "type": ReminderType.expiration_date,
                        "threshold": days,
                        "expires_at": user.expire,
                    }
                )
            # Bulk create notification reminders
            if reminder_data:
                await bulk_create_notification_reminders(db, reminder_data)

            if webhook_data:
                await notification.wh.bulk_notify(webhook_data)


if runtime_settings.role.runs_scheduler:
    now = dt.now(UTC)
    interval = int(job_settings.review_subscriptions_interval / 4)

    # Register each job separately
    scheduler.add_job(
        expire_users_job,
        "interval",
        seconds=job_settings.review_subscriptions_interval,
        coalesce=True,
        max_instances=1,
        start_date=now,
        id="expire_users",
        replace_existing=True,
    )
    scheduler.add_job(
        limit_users_job,
        "interval",
        seconds=job_settings.review_subscriptions_interval,
        coalesce=True,
        max_instances=1,
        start_date=now + td(seconds=interval),
        id="limit_users",
        replace_existing=True,
    )
    scheduler.add_job(
        usage_percent_notification_job,
        "interval",
        seconds=job_settings.review_subscriptions_interval,
        coalesce=True,
        max_instances=1,
        start_date=now + td(seconds=interval * 2),
        id="usage_percent_notification",
        replace_existing=True,
    )
    scheduler.add_job(
        days_left_notification_job,
        "interval",
        seconds=job_settings.review_subscriptions_interval,
        coalesce=True,
        max_instances=1,
        start_date=now + td(seconds=interval * 3),
        id="days_left_notification",
        replace_existing=True,
    )
