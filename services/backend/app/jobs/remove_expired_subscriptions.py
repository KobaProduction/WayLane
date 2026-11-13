import asyncio

from app import notification, scheduler
from app.db import GetDB
from app.db.crud.subscription import autodelete_expired_subscriptions
from app.jobs.dependencies import SYSTEM_ADMIN
from app.subscription_delivery import reconcile_subscriptions
from app.utils.logger import get_logger
from config import job_settings, runtime_settings, user_cleanup_settings

logger = get_logger("jobs")


async def remove_expired_subscriptions():
    async with GetDB() as db:
        deleted_users = await autodelete_expired_subscriptions(db, user_cleanup_settings.include_limited_accounts)
        if deleted_users:
            # Deleted subscriptions must be removed from autonomous Xray
            # admission at the next WSS desired generation.
            await reconcile_subscriptions()

        for user in deleted_users:
            asyncio.create_task(notification.remove_subscription(user=user, by=SYSTEM_ADMIN))
            logger.info(f"Subscription `{user.uuid}` has been deleted due to expiration.")


if runtime_settings.role.runs_scheduler:
    scheduler.add_job(
        remove_expired_subscriptions,
        "interval",
        coalesce=True,
        seconds=job_settings.remove_expired_subscriptions_interval,
        max_instances=1,
        id="remove_expired_subscriptions",
        replace_existing=True,
    )
