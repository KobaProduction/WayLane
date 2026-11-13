"""Epoch-aware cumulative Xray StatsService samples over authenticated WSS.

By default, all counters are diagnostic only. The separate explicit Backend
billing gate authorizes exactly-once incremental deltas after a new baseline
has been established and the actual client traffic has been accepted.
Neither mode resets private Xray StatsService counters.
"""

import math
from datetime import UTC, datetime, timedelta
from uuid import UUID

from pydantic import UUID4, BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import and_, insert, or_, select, update
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
    """Deduplicate Xray counters; optionally bill incremental verified deltas.

    Commercial writes are disabled by default. The first accepted sample
    after enabling billing establishes a baseline, so existing read-only
    observations are never back-billed. Cursor and subscription writes share
    a transaction; a delivery retry cannot double-charge a committed delta.
    """
    from app.db.models import (
        Admin,
        AdminRole,
        AdminStatus,
        Group,
        ProxyInbound,
        SubscriptionStatus,
        inbounds_groups_association,
        node_agent_xray_usage_hours,
        subscriptions_groups_association,
    )
    from app.node_subscription_desired import enqueue_managed_subscription_changes
    from config import usage_settings

    uuids = [UUID(str(item.subscription_uuid)) for item in snapshot.samples]
    # Keep the same lock order as commercial writes: subscription rows first,
    # then Node identity/generation. This avoids inversions with the API
    # (which updates a subscription before notifying the Node).
    if db.bind is not None and db.bind.dialect.name == "sqlite":
        await begin_immediate_if_sqlite(db)
    else:
        await db.execute(
            select(Subscription.uuid)
            .where(Subscription.uuid.in_(uuids))
            .order_by(Subscription.uuid)
            .with_for_update()
        )
    node = (await db.execute(
        select(node_agent_registrations.c.status)
        .where(node_agent_registrations.c.node_uuid == node_uuid)
        .with_for_update()
    )).scalar_one_or_none()
    if node != "approved":
        raise ValueError("Only approved Nodes may report Xray traffic")
    if not await db.scalar(
        select(node_agent_xray_managed_inbounds.c.node_uuid)
        .where(node_agent_xray_managed_inbounds.c.node_uuid == node_uuid)
        .limit(1)
    ):
        return {"updated": 0, "ignored": len(snapshot.samples), "billing": False}

    valid = set((await db.execute(
        select(Subscription.uuid).where(Subscription.uuid.in_(uuids))
    )).scalars())
    cursors = {
        row["subscription_uuid"]: row
        for row in (await db.execute(
            select(node_agent_xray_usage_cursors)
            .where(
                node_agent_xray_usage_cursors.c.node_uuid == node_uuid,
                node_agent_xray_usage_cursors.c.subscription_uuid.in_(uuids),
            ).with_for_update()
        )).mappings()
    }
    now = datetime.now(UTC)
    # A node must not charge any UUID that was never assigned to one of its
    # explicitly managed inbound tags by the subscription/group catalog.
    owned: set[UUID] = set()
    if usage_settings.xray_usage_billing_enabled:
        owned = set((await db.execute(
            select(Subscription.uuid)
            .join(subscriptions_groups_association,
                  subscriptions_groups_association.c.subscription_uuid == Subscription.uuid)
            .join(Group, Group.id == subscriptions_groups_association.c.groups_id)
            .join(inbounds_groups_association,
                  inbounds_groups_association.c.group_id == Group.id)
            .join(ProxyInbound, ProxyInbound.id == inbounds_groups_association.c.inbound_id)
            .join(node_agent_xray_managed_inbounds,
                  node_agent_xray_managed_inbounds.c.inbound_tag == ProxyInbound.tag)
            .outerjoin(Admin, Admin.id == Subscription.admin_id)
            .outerjoin(AdminRole, AdminRole.id == Admin.role_id)
            .where(node_agent_xray_managed_inbounds.c.node_uuid == node_uuid)
            .where(Subscription.uuid.in_(uuids))
            .where(Group.is_disabled.is_(False))
            .where(Subscription.entitlement == "active")
            .where(Subscription.status.in_([SubscriptionStatus.active, SubscriptionStatus.on_hold]))
            .where(or_(
                Admin.id.is_(None),
                Admin.status == AdminStatus.active,
                and_(Admin.status == AdminStatus.limited,
                     AdminRole.disconnect_users_when_limited.is_(False)),
                and_(Admin.status == AdminStatus.disabled,
                     AdminRole.disconnect_users_when_disabled.is_(False)),
            ))
            .where((Subscription._expire.is_(None)) | (Subscription._expire > now))
            .distinct()
        )).scalars())
    updated = ignored = billed = 0
    must_reconcile = False

    for item in snapshot.samples:
        sid = UUID(str(item.subscription_uuid))
        if sid not in valid:
            ignored += 1
            continue
        previous = cursors.get(sid)
        if previous:
            epoch = previous["epoch_started_at"]
            if epoch.tzinfo is None:
                epoch = epoch.replace(tzinfo=UTC)
            epoch = epoch.astimezone(UTC)
            if snapshot.epoch < epoch:
                ignored += 1
                continue
            if snapshot.epoch == epoch:
                if item.uplink < previous["uplink"] or item.downlink < previous["downlink"]:
                    ignored += 1
                    continue
                delta_up = item.uplink - previous["uplink"]
                delta_down = item.downlink - previous["downlink"]
            else:
                delta_up, delta_down = item.uplink, item.downlink
            new_up = previous["total_uplink"] + delta_up
            new_down = previous["total_downlink"] + delta_down
        else:
            delta_up, delta_down = item.uplink, item.downlink
            new_up, new_down = item.uplink, item.downlink
        if new_up > MAX_TOTAL_BYTES or new_down > MAX_TOTAL_BYTES:
            ignored += 1
            continue

        eligible = usage_settings.xray_usage_billing_enabled and sid in owned
        # On an existing read-only cursor, or on a first sample, enable
        # billing at the *current* totals. No past bytes are charged.
        baseline_ready = bool(previous["billing_ready"]) if previous else False
        previous_accounted_up = previous["accounted_uplink"] if previous else 0
        previous_accounted_down = previous["accounted_downlink"] if previous else 0
        charge = 0
        if eligible and baseline_ready:
            if new_up < previous_accounted_up or new_down < previous_accounted_down:
                ignored += 1
                continue
            charge = (new_up - previous_accounted_up) + (new_down - previous_accounted_down)
        if charge:
            # Trigger another desired generation only on a quota transition;
            # every regular stats heartbeat must not restart/reconfigure Xray.
            old_used, limit, admin_id = (await db.execute(
                select(Subscription.used_traffic, Subscription.data_limit, Subscription.admin_id)
                .where(Subscription.uuid == sid)
            )).one()
            quota_reached = bool(limit and limit > 0 and old_used < limit <= old_used + charge)
            # Atomic SQL addition avoids lost updates with commercial API
            # resets. A 64-bit overflow aborts the whole WSS transaction.
            stmt = (
                update(Subscription)
                .where(Subscription.uuid == sid)
                .where(Subscription.used_traffic <= MAX_TOTAL_BYTES - charge)
                .values(
                    used_traffic=Subscription.used_traffic + charge,
                    online_at=now,
                )
            )
            result = await db.execute(stmt)
            if result.rowcount != 1:
                raise ValueError("Xray commercial counter overflow or missing subscription")
            if admin_id is not None:
                updated_admin = await db.execute(
                    update(Admin)
                    .where(Admin.id == admin_id)
                    .where(Admin.used_traffic <= MAX_TOTAL_BYTES - charge)
                    .values(used_traffic=Admin.used_traffic + charge)
                )
                if updated_admin.rowcount != 1:
                    raise ValueError("Xray commercial admin counter overflow or missing administrator")
            # Quota admission is reevaluated at the next WSS desired update.
            # Keep the counter+generation in one DB transaction.
            must_reconcile = must_reconcile or quota_reached
            billed += charge

        values = {
            "epoch_started_at": snapshot.epoch,
            "uplink": item.uplink,
            "downlink": item.downlink,
            "total_uplink": new_up,
            "total_downlink": new_down,
            "billing_ready": eligible,
            "accounted_uplink": new_up,
            "accounted_downlink": new_down,
            "observed_at": now,
        }
        if delta_up or delta_down:
            hour = now.replace(minute=0, second=0, microsecond=0)
            hourly = (await db.execute(
                select(node_agent_xray_usage_hours)
                .where(
                    node_agent_xray_usage_hours.c.node_uuid == node_uuid,
                    node_agent_xray_usage_hours.c.subscription_uuid == sid,
                    node_agent_xray_usage_hours.c.bucket_start == hour,
                ).with_for_update()
            )).mappings().one_or_none()
            if hourly:
                if (hourly["uplink"] > MAX_TOTAL_BYTES - delta_up
                        or hourly["downlink"] > MAX_TOTAL_BYTES - delta_down):
                    raise ValueError("Hourly Xray usage exceeds bigint bounds")
                await db.execute(
                    update(node_agent_xray_usage_hours)
                    .where(
                        node_agent_xray_usage_hours.c.node_uuid == node_uuid,
                        node_agent_xray_usage_hours.c.subscription_uuid == sid,
                        node_agent_xray_usage_hours.c.bucket_start == hour,
                    ).values(
                        uplink=node_agent_xray_usage_hours.c.uplink + delta_up,
                        downlink=node_agent_xray_usage_hours.c.downlink + delta_down,
                    )
                )
            else:
                await db.execute(
                    insert(node_agent_xray_usage_hours).values(
                        node_uuid=node_uuid, subscription_uuid=sid,
                        bucket_start=hour, uplink=delta_up, downlink=delta_down,
                    )
                )

        if previous:
            await db.execute(
                update(node_agent_xray_usage_cursors)
                .where(
                    node_agent_xray_usage_cursors.c.node_uuid == node_uuid,
                    node_agent_xray_usage_cursors.c.subscription_uuid == sid,
                ).values(**values)
            )
        else:
            await db.execute(
                insert(node_agent_xray_usage_cursors)
                .values(node_uuid=node_uuid, subscription_uuid=sid, **values)
            )
        updated += 1

    if must_reconcile:
        await enqueue_managed_subscription_changes(db, commit=False)
    await db.commit()
    return {
        "updated": updated, "ignored": ignored,
        "billing": usage_settings.xray_usage_billing_enabled,
        "billed_bytes": billed,
    }
