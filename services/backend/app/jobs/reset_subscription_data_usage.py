import asyncio
from datetime import UTC, datetime as dt, timedelta as td

from app import notification, scheduler
from app.db import GetDB
from app.db.crud.subscription import bulk_reset_subscription_data_usage, get_subscriptions_to_reset_data_usage
from app.jobs.dependencies import SYSTEM_ADMIN
from app.node.sync import sync_subscriptions
from app.operation import OperatorType
from app.operation.subscription_management import SubscriptionManagementOperation
from app.utils.logger import get_logger
from config import job_settings, runtime_settings, usage_settings

logger = get_logger("jobs")
user_operator = SubscriptionManagementOperation(operator_type=OperatorType.SYSTEM)


async def reset_data_usage():
    async with GetDB() as db:
        users = await get_subscriptions_to_reset_data_usage(db)
        old_statuses = {user.uuid: user.status for user in users}

        updated_users = await bulk_reset_subscription_data_usage(
            db,
            users,
            clean_chart_data=usage_settings.reset_user_usage_clean_chart_data,
        )

        await sync_subscriptions(updated_users)
        for db_user in updated_users:
            user = await user_operator.validate_subscription(db_user)
            asyncio.create_task(notification.reset_subscription_data_usage(user, SYSTEM_ADMIN))

            if old_statuses.get(user.uuid) != user.status:
                asyncio.create_task(notification.subscription_status_change(user, SYSTEM_ADMIN))

            logger.info(f'Subscription data usage reset for Subscription "{user.uuid}"')


if runtime_settings.role.runs_scheduler:
    scheduler.add_job(
        reset_data_usage,
        "interval",
        seconds=job_settings.reset_subscription_data_usage_interval,
        coalesce=True,
        start_date=dt.now(UTC) + td(minutes=1),
        max_instances=1,
        id="reset_subscription_data_usage",
        replace_existing=True,
    )
