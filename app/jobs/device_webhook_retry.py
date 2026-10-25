"""Retry due device webhooks via WayLane's existing scheduler leader only."""

from app import scheduler
from app.subscription.device_webhook import retry_due_device_events
from config import runtime_settings

# The normal scheduler-role leadership gates prevent every API worker from
# polling independently. The DB's per-attempt fencing remains authoritative.
if runtime_settings.role.runs_scheduler:
    scheduler.add_job(
        retry_due_device_events,
        "interval",
        seconds=30,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=60,
        id="retry_device_webhooks",
        replace_existing=True,
    )
