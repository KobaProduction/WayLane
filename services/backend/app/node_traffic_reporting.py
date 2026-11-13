"""Read-only Xray StatsService samples over authenticated binary Node WSS.

Never reset counters or write Subscription.used_traffic here. Cumulative
samples are deduplicated against a durable per-(Node, Subscription) cursor,
using Docker's Xray start timestamp as the counter epoch. A runtime restart
with a newer epoch adds only the new counter values, and stale old-epoch
frames cannot resurrect already-accounted statistics. Live traffic/billing
acceptance is a separate gate.
"""

import math
from datetime import UTC, datetime, timedelta
from uuid import UUID

from pydantic import UUID4, BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import begin_immediate_if_sqlite
from app.db.models import (
    Subscription,
    node_agent_registrations,
    node_agent_xray_managed_inbounds,
    node_agent_xray_usage_cursors,
)

MAX_SAMPLE_BYTES = 9_007_199_254_740_991  # Protobuf Struct exact integer bound.
MAX_TOTAL_BYTES = 9_223_372_036_854_775_807  # Postgres BIGINT bound.


class XrayTrafficSample(BaseModel):
    model_config = ConfigDict(extra="forbid")
    subscription_uuid: UUID4
    uplink: int = Field(ge=0, le=MAX_SAMPLE_BYTES)
    downlink: int = Field(ge=0, le=MAX_SAMPLE_BYTES)

    @field_validator("uplink", "downlink", mode="before")
    @classmethod
    def exact_byte_count(cls, value):
        if (isinstance(value, bool) or not isinstance(value, (int, float))
                or isinstance(value, float) and (not math.isfinite(value) or not value.is_integer())):
            raise ValueError("Xray traffic counters must be exact nonnegative integer bytes")
        return value


class XrayTrafficSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")
    epoch: datetime
    samples: list[XrayTrafficSample] = Field(min_length=1, max_length=1024)

    @field_validator("epoch")
    @classmethod
    def real_start_time(cls, value: datetime) -> datetime:
        if (value.tzinfo is None or value.utcoffset() is None
                or value < datetime(2020, 1, 1, tzinfo=UTC)
                or value > datetime.now(UTC) + timedelta(minutes=2)):
            raise ValueError("Xray start epoch must be a past timezone-aware timestamp")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def unique_subscription_records(self):
        if len({item.subscription_uuid for item in self.samples}) != len(self.samples):
            raise ValueError("Duplicate subscription UUID in the Xray traffic snapshot")
        return self


async def record_xray_traffic(db: AsyncSession, node_uuid: str, snapshot: XrayTrafficSnapshot) -> dict:
    """Deduplicate a bounded WSS sample without changing commercial usage."""
    await begin_immediate_if_sqlite(db)
    # PostgreSQL row lock serializes *all* concurrent WSS usage streams for
    # this identity, including the first sample with no existing cursor row.
    node = (await db.execute(select(node_agent_registrations.c.status)
                             .where(node_agent_registrations.c.node_uuid == node_uuid)
                             .with_for_update())).scalar_one_or_none()
    if node != "approved":
        raise ValueError("Only approved Nodes may report Xray traffic")
    if not await db.scalar(select(node_agent_xray_managed_inbounds.c.node_uuid)
                           .where(node_agent_xray_managed_inbounds.c.node_uuid == node_uuid).limit(1)):
        # A healthy Core may expose manual stats even when no inbound is
        # opted into managed admission. Never disconnect its WSS telemetry.
        return {"updated": 0, "ignored": len(snapshot.samples)}

    uuids = [UUID(str(item.subscription_uuid)) for item in snapshot.samples]
    valid = set((await db.execute(select(Subscription.uuid).where(Subscription.uuid.in_(uuids)))).scalars())
    cursors = {
        row["subscription_uuid"]: row
        for row in (await db.execute(select(node_agent_xray_usage_cursors)
                   .where(node_agent_xray_usage_cursors.c.node_uuid == node_uuid,
                          node_agent_xray_usage_cursors.c.subscription_uuid.in_(uuids))
                   .with_for_update())).mappings()
    }
    updated = ignored = 0
    now = datetime.now(UTC)
    for item in snapshot.samples:
        sid = UUID(str(item.subscription_uuid))
        if sid not in valid:
            ignored += 1
            continue
        previous = cursors.get(sid)
        if previous:
            epoch = previous["epoch_started_at"]
            if epoch.tzinfo is None:
                epoch = epoch.replace(tzinfo=UTC)  # SQLite strips the timezone.
            epoch = epoch.astimezone(UTC)
            if snapshot.epoch < epoch:
                ignored += 1  # Delayed frame from a previous Xray process.
                continue
            if snapshot.epoch == epoch:
                if item.uplink < previous["uplink"] or item.downlink < previous["downlink"]:
                    # Counter reset without a newer Docker epoch is ambiguous.
                    # Ignore rather than double count or tear down healthy WSS.
                    ignored += 1
                    continue
                up_delta = item.uplink - previous["uplink"]
                down_delta = item.downlink - previous["downlink"]
            else:
                up_delta, down_delta = item.uplink, item.downlink
            new_up = previous["total_uplink"] + up_delta
            new_down = previous["total_downlink"] + down_delta
        else:
            new_up, new_down = item.uplink, item.downlink
        if new_up > MAX_TOTAL_BYTES or new_down > MAX_TOTAL_BYTES:
            ignored += 1
            continue
        values = {
            "epoch_started_at": snapshot.epoch,
            "uplink": item.uplink,
            "downlink": item.downlink,
            "total_uplink": new_up,
            "total_downlink": new_down,
            "observed_at": now,
        }
        if previous:
            await db.execute(update(node_agent_xray_usage_cursors)
                             .where(node_agent_xray_usage_cursors.c.node_uuid == node_uuid,
                                    node_agent_xray_usage_cursors.c.subscription_uuid == sid)
                             .values(**values))
        else:
            await db.execute(insert(node_agent_xray_usage_cursors)
                             .values(node_uuid=node_uuid, subscription_uuid=sid, **values))
        updated += 1
    await db.commit()
    return {"updated": updated, "ignored": ignored}
