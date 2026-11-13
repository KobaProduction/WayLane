"""
Comprehensive timezone filtering tests for usage statistics functions.

Tests verify that the UTC conversion fix correctly filters records by timezone-aware start/end dates.
This includes strict testing with multiple data rows, edge cases for each period, and expected responses.
"""

from datetime import UTC, datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.db.crud.admin import get_admin_usages
from app.db.crud.subscription import (
    get_all_subscriptions_usages,
    get_subscription_count_metric_stats,
    get_subscription_usages,
)
from app.db.models import (
    Admin,
    CoreConfig,
    Node,
    NodeSubscriptionUsage,
    Subscription,
    SubscriptionStatus,
)
from app.models.proxy import ProxyTable
from app.models.stats import (
    Period,
    SubscriptionCountMetric,
    SubscriptionCountMetricStatsList,
    SubscriptionUsageStatsList,
)
from tests.api import TestSession


async def setup_test_data(session, test_suffix=""):
    """Create admin, user, and node for tests within an existing session.

    Args:
        session: SQLAlchemy async session
        test_suffix: Optional suffix to make usernames unique across tests
    """
    # Generate unique identifiers to avoid UNIQUE constraint violations
    unique_id = str(uuid4())[:8]
    if test_suffix:
        unique_id = f"{test_suffix}_{unique_id}"

    admin = Admin(username=f"admin_{unique_id}", hashed_password="secret", role_id=3)
    session.add(admin)
    await session.flush()

    user = Subscription(uuid=uuid4(), admin_id=admin.id, proxy_settings=ProxyTable().dict(no_obj=True))
    session.add(user)
    await session.flush()

    node = Node(
        name=f"node_{unique_id}",
        address="127.0.0.1",
        port=8080,
        api_port=62051,
        server_ca="ca",
        api_key="key",
        core_config_id=None,
    )
    session.add(node)
    await session.flush()
    await session.commit()
    await session.refresh(admin)
    await session.refresh(user)
    await session.refresh(node)

    return admin.id, user.uuid, node.id




class TestGetUserUsagesTimezone:
    """Test get_user_usages with timezone-aware filtering."""

    @pytest.mark.asyncio
    async def test_user_usages_timezone_filtering_strict(self):
        """
        Strict test: Multiple data rows with Tehran timezone.

        Verifies correct filtering for user usage statistics.
        """
        async with TestSession() as session:
            _admin_id, subscription_uuid, node_id = await setup_test_data(session)

            # Inject 8 data points: 2 before, 5 in range, 1 after
            timestamps_utc = [
                datetime(2026, 7, 9, 20, 0, 0, tzinfo=UTC),  # BEFORE
                datetime(2026, 7, 9, 20, 15, 0, tzinfo=UTC),  # BEFORE
                datetime(2026, 7, 9, 20, 30, 0, tzinfo=UTC),  # IN RANGE ✓
                datetime(2026, 7, 9, 20, 45, 0, tzinfo=UTC),  # IN RANGE ✓
                datetime(2026, 7, 9, 21, 30, 0, tzinfo=UTC),  # IN RANGE ✓
                datetime(2026, 7, 9, 22, 15, 0, tzinfo=UTC),  # IN RANGE ✓
                datetime(2026, 7, 9, 23, 15, 0, tzinfo=UTC),  # IN RANGE ✓
                datetime(2026, 7, 10, 0, 0, 0, tzinfo=UTC),  # AFTER
            ]

            for ts in timestamps_utc:
                record = NodeSubscriptionUsage(
                    created_at=ts,
                    subscription_uuid=subscription_uuid,
                    node_id=node_id,
                    used_traffic=5000000,
                )
                session.add(record)
            await session.commit()

            tehran_tz = timezone(timedelta(hours=3, minutes=30))
            start = datetime(2026, 7, 10, 0, 0, 0, tzinfo=tehran_tz)
            end = datetime(2026, 7, 10, 3, 0, 0, tzinfo=tehran_tz)

            result = await get_subscription_usages(
                session,
                subscription_uuid=subscription_uuid,
                start=start,
                end=end,
                period=Period.hour,
            )

            assert isinstance(result, SubscriptionUsageStatsList)
            assert result.stats is not None
            assert -1 in result.stats
            stats = result.stats[-1]

            # Should have exactly 3 periods (not 8, not more)
            assert len(stats) == 3, f"Expected 3 periods, got {len(stats)}"

            # Expected: 5 in-range records with used_traffic=5000000 each.
            # Bucket totals in Tehran are [10000000, 10000000, 5000000].
            expected_bucket_totals = [10000000, 10000000, 5000000]
            expected_total_traffic = sum(expected_bucket_totals)  # 25000000

            total_traffic = 0

            # All periods should be within requested range
            for i, stat in enumerate(stats):
                assert stat.period_start >= start
                assert stat.period_start < end

                # STRICT: Validate exact per-bucket traffic
                assert stat.total_traffic == expected_bucket_totals[i], (
                    f"Period {i}: expected total_traffic={expected_bucket_totals[i]}, got {stat.total_traffic}"
                )
                total_traffic += stat.total_traffic

            # STRICT: Total must match exactly all in-range records
            assert total_traffic == expected_total_traffic, (
                f"Expected total_traffic={expected_total_traffic}, got {total_traffic}"
            )

    @pytest.mark.asyncio
    @pytest.mark.parametrize("period", [Period.hour, Period.day, Period.month])
    async def test_user_usages_multiple_periods_strict(self, period):
        """
        Strict test: Multiple periods with proper data distribution.
        """
        async with TestSession() as session:
            _admin_id, subscription_uuid, node_id = await setup_test_data(session)

            # Create data spanning 3 months
            start_utc = datetime(2026, 8, 1, 0, 0, 0, tzinfo=UTC)
            end_utc = datetime(2026, 11, 1, 0, 0, 0, tzinfo=UTC)

            # Add records at various points
            current = start_utc
            while current < end_utc:
                record = NodeSubscriptionUsage(
                    created_at=current,
                    subscription_uuid=subscription_uuid,
                    node_id=node_id,
                    used_traffic=5000000,
                )
                session.add(record)
                current += timedelta(days=5)
            await session.commit()

            result = await get_subscription_usages(
                session,
                subscription_uuid=subscription_uuid,
                start=start_utc,
                end=end_utc,
                period=period,
            )

            assert result.stats is not None
            assert -1 in result.stats
            stats = result.stats[-1]

            # Expected: Records added every 5 days starting from Feb 1
            # In the Feb 1 - May 1 range, we should have multiple records
            # Each record has 5000000 traffic
            total_traffic = 0

            # All stats must be within range
            for stat in stats:
                assert stat.period_start >= start_utc
                assert stat.period_start < end_utc

                # STRICT: Validate traffic is exactly from our records
                assert stat.total_traffic > 0, "Total traffic should be > 0"
                # Each record injected has 5000000, so totals must be multiples of that
                assert stat.total_traffic % 5000000 == 0, f"Traffic {stat.total_traffic} is not a multiple of 5000000"
                total_traffic += stat.total_traffic

            # STRICT: Total traffic should be sum of all records
            # 73 days / 5 = ~14-15 records * 5000000
            assert total_traffic > 0, "Should have non-zero total traffic"
            assert total_traffic % 5000000 == 0, "Total traffic must be multiple of 5000000"


class TestGetAllUsersUsagesTimezone:
    """Test get_all_users_usages with timezone-aware filtering."""

    @pytest.mark.asyncio
    async def test_all_users_usages_timezone_filtering_strict(self):
        """
        Strict test: Validate timezone filtering for all users aggregation.
        """
        async with TestSession() as session:
            admin_id, subscription_uuid, node_id = await setup_test_data(session)

            # Inject data with mixture of before and in-range records
            before_timestamps = [
                datetime(2026, 9, 9, 20, 0, 0, tzinfo=UTC),
                datetime(2026, 9, 9, 20, 30, 0, tzinfo=UTC),
            ]

            in_range_timestamps = [
                datetime(2026, 9, 9, 20, 45, 0, tzinfo=UTC),
                datetime(2026, 9, 9, 21, 30, 0, tzinfo=UTC),
                datetime(2026, 9, 9, 22, 15, 0, tzinfo=UTC),
            ]

            all_timestamps = before_timestamps + in_range_timestamps
            for ts in all_timestamps:
                record = NodeSubscriptionUsage(
                    created_at=ts,
                    subscription_uuid=subscription_uuid,
                    node_id=node_id,
                    used_traffic=5000000,
                )
                session.add(record)
            await session.commit()

            tehran_tz = timezone(timedelta(hours=3, minutes=30))
            start = datetime(2026, 9, 10, 0, 0, 0, tzinfo=tehran_tz)
            end = datetime(2026, 9, 10, 3, 0, 0, tzinfo=tehran_tz)

            result = await get_all_subscriptions_usages(
                session,
                admins=[(await session.execute(select(Admin.username).where(Admin.id == admin_id))).scalar_one()],
                start=start,
                end=end,
                period=Period.hour,
            )

            assert result.stats is not None

            # Expected rows in [start, end): 20:30, 20:45, 21:30, 22:15 UTC.
            expected_total_traffic = 4 * 5000000  # 20000000

            total_traffic = 0

            # Validate all stats are within range - no data before start
            # get_all_users_usages returns dict[user_id, list[UserUsageStat]] or dict[user_id, dict[node_id, list]]
            for user_stats in result.stats.values():
                # Handle both structures: list or dict
                if isinstance(user_stats, dict):
                    for stats_list in user_stats.values():
                        for stat in stats_list:
                            assert stat.period_start >= start, (
                                f"BUG: Got data from before start! period_start {stat.period_start} < start {start}"
                            )
                            # STRICT: Validate traffic
                            assert stat.total_traffic > 0, f"Traffic should be > 0, got {stat.total_traffic}"
                            total_traffic += stat.total_traffic
                elif isinstance(user_stats, list):
                    for stat in user_stats:
                        assert stat.period_start >= start, (
                            f"BUG: Got data from before start! period_start {stat.period_start} < start {start}"
                        )
                        # STRICT: Validate traffic
                        assert stat.total_traffic > 0, f"Traffic should be > 0, got {stat.total_traffic}"
                        total_traffic += stat.total_traffic

            # STRICT: Total must match in-range records
            assert total_traffic == expected_total_traffic, (
                f"Expected total_traffic={expected_total_traffic}, got {total_traffic}"
            )

    @pytest.mark.asyncio
    async def test_all_users_usages_group_by_admin_and_core_filter(self):
        """
        Grouping by admin keys stats by admin id, and core_id only counts nodes of that core.
        """
        async with TestSession() as session:
            first_admin_id, first_user_id, core_node_id = await setup_test_data(session, "first")
            second_admin_id, second_user_id, other_node_id = await setup_test_data(session, "second")

            core = CoreConfig(name=f"core_{uuid4().hex[:8]}", config={})
            session.add(core)
            await session.flush()
            core_id = core.id
            core_node = await session.get(Node, core_node_id)
            core_node.core_config_id = core_id
            await session.commit()

            ts = datetime(2026, 9, 9, 21, 30, 0, tzinfo=UTC)
            records = [
                (first_user_id, core_node_id, 1000),
                (first_user_id, other_node_id, 200),
                (second_user_id, core_node_id, 30),
                (second_user_id, other_node_id, 4),
            ]
            for subscription_uuid, node_id, used_traffic in records:
                session.add(
                    NodeSubscriptionUsage(created_at=ts, subscription_uuid=subscription_uuid, node_id=node_id, used_traffic=used_traffic)
                )
            await session.commit()

            admin_usernames = list(
                (
                    await session.execute(select(Admin.username).where(Admin.id.in_([first_admin_id, second_admin_id])))
                ).scalars()
            )
            tehran_tz = timezone(timedelta(hours=3, minutes=30))
            start = datetime(2026, 9, 10, 0, 0, 0, tzinfo=tehran_tz)
            end = datetime(2026, 9, 10, 3, 0, 0, tzinfo=tehran_tz)

            result = await get_all_subscriptions_usages(
                session,
                admins=admin_usernames,
                start=start,
                end=end,
                period=Period.hour,
                group_by_admin=True,
            )

            assert set(result.stats.keys()) == {first_admin_id, second_admin_id}
            assert sum(stat.total_traffic for stat in result.stats[first_admin_id]) == 1200
            assert sum(stat.total_traffic for stat in result.stats[second_admin_id]) == 34

            core_result = await get_all_subscriptions_usages(
                session,
                admins=admin_usernames,
                start=start,
                end=end,
                period=Period.hour,
                core_id=core_id,
                group_by_admin=True,
            )

            assert sum(stat.total_traffic for stat in core_result.stats[first_admin_id]) == 1000
            assert sum(stat.total_traffic for stat in core_result.stats[second_admin_id]) == 30


class TestGetAdminUsagesTimezone:
    """Test get_admin_usages with timezone-aware filtering."""

    @pytest.mark.asyncio
    async def test_admin_usages_timezone_filtering_strict(self):
        """
        Strict test: Admin-level aggregation with multiple users and data rows.
        """
        async with TestSession() as session:
            admin_id, subscription_uuid, node_id = await setup_test_data(session)

            # A second UUID subscription belongs to the same administrator.
            user2 = Subscription(
                uuid=uuid4(), admin_id=admin_id, proxy_settings=ProxyTable().dict(no_obj=True)
            )
            session.add(user2)
            await session.flush()

            # Inject 8 data points for each user
            timestamps_utc = [
                datetime(2026, 10, 9, 20, 0, 0, tzinfo=UTC),  # BEFORE
                datetime(2026, 10, 9, 20, 15, 0, tzinfo=UTC),  # BEFORE
                datetime(2026, 10, 9, 20, 30, 0, tzinfo=UTC),  # IN RANGE
                datetime(2026, 10, 9, 20, 45, 0, tzinfo=UTC),  # IN RANGE
                datetime(2026, 10, 9, 21, 30, 0, tzinfo=UTC),  # IN RANGE
                datetime(2026, 10, 9, 22, 15, 0, tzinfo=UTC),  # IN RANGE
                datetime(2026, 10, 9, 23, 15, 0, tzinfo=UTC),  # IN RANGE
                datetime(2026, 10, 10, 0, 0, 0, tzinfo=UTC),  # AFTER
            ]

            for ts in timestamps_utc:
                # User 1
                record1 = NodeSubscriptionUsage(
                    created_at=ts,
                    subscription_uuid=subscription_uuid,
                    node_id=node_id,
                    used_traffic=5000000,
                )
                session.add(record1)

                # User 2
                record2 = NodeSubscriptionUsage(
                    created_at=ts,
                    subscription_uuid=user2.uuid,
                    node_id=node_id,
                    used_traffic=3000000,
                )
                session.add(record2)
            await session.commit()

            tehran_tz = timezone(timedelta(hours=3, minutes=30))
            start = datetime(2026, 10, 10, 0, 0, 0, tzinfo=tehran_tz)
            end = datetime(2026, 10, 10, 3, 0, 0, tzinfo=tehran_tz)

            result = await get_admin_usages(
                session,
                admin_id=admin_id,
                start=start,
                end=end,
                period=Period.hour,
            )

            assert result.stats is not None
            assert -1 in result.stats  # Default node_id when not grouped by node
            stats = result.stats[-1]

            # Should have exactly 3 periods for hour-level grouping in 3-hour range
            assert len(stats) == 3, f"Expected 3 periods, got {len(stats)}"

            # Expected: 5 in-range rows per user in [start, end), for two users:
            # user1: 5 * 5000000 = 25000000
            # user2: 5 * 3000000 = 15000000
            # total: 40000000
            expected_bucket_totals = [16000000, 16000000, 8000000]
            expected_total_traffic = sum(expected_bucket_totals)  # 40000000
            total_traffic = 0

            # All periods should be within range
            for i, stat in enumerate(stats):
                assert stat.period_start >= start
                assert stat.period_start < end

                # STRICT: Validate exact per-bucket traffic
                assert stat.total_traffic == expected_bucket_totals[i], (
                    f"Period {i}: expected total_traffic={expected_bucket_totals[i]}, got {stat.total_traffic}"
                )
                total_traffic += stat.total_traffic

            # STRICT: Total must match exactly all in-range records
            assert total_traffic == expected_total_traffic, (
                f"Expected total_traffic={expected_total_traffic}, got {total_traffic}"
            )

    @pytest.mark.asyncio
    @pytest.mark.parametrize("period", [Period.hour, Period.day])
    async def test_admin_usages_multiple_periods_strict(self, period):
        """
        Strict test: Multiple periods with admin-level aggregation.
        """
        async with TestSession() as session:
            admin_id, subscription_uuid, node_id = await setup_test_data(session)

            start_utc = datetime(2026, 11, 1, 0, 0, 0, tzinfo=UTC)
            end_utc = datetime(2026, 11, 15, 0, 0, 0, tzinfo=UTC)

            # Create records spanning the range
            current = start_utc
            while current < end_utc:
                record = NodeSubscriptionUsage(
                    created_at=current,
                    subscription_uuid=subscription_uuid,
                    node_id=node_id,
                    used_traffic=5000000,
                )
                session.add(record)
                current += timedelta(hours=6)
            await session.commit()

            result = await get_admin_usages(
                session,
                admin_id=admin_id,
                start=start_utc,
                end=end_utc,
                period=period,
            )

            assert result.stats is not None
            assert -1 in result.stats
            stats = result.stats[-1]

            # Expected: Records added every 6 hours from Feb 1 - Feb 15
            # Feb 1-15 is 14 days = 336 hours / 6 hours = 56 records
            # Each record has 5000000 traffic
            total_traffic = 0

            # All periods must be within range
            for stat in stats:
                assert stat.period_start >= start_utc
                assert stat.period_start < end_utc

                # STRICT: Validate traffic is from records
                assert stat.total_traffic > 0, f"Traffic should be > 0, got {stat.total_traffic}"
                # Each record is 5000000, so totals must be multiples
                assert stat.total_traffic % 5000000 == 0, f"Traffic {stat.total_traffic} is not a multiple of 5000000"
                total_traffic += stat.total_traffic

            # STRICT: Total traffic should be from all records
            assert total_traffic > 0, "Should have non-zero total traffic"
            assert total_traffic % 5000000 == 0, "Total traffic must be multiple of 5000000"


class TestGetUserCountMetricStats:
    """Test user activity/status metric aggregation over node_user_usages."""

    @pytest.mark.asyncio
    async def test_timezone_filtering_distinct_counts_and_current_statuses(self):
        async with TestSession() as session:
            admin_id, active_user_id, node_id = await setup_test_data(session)
            admin_username = (await session.execute(select(Admin.username).where(Admin.id == admin_id))).scalar_one()

            expired_user = Subscription(
                uuid=uuid4(),
                admin_id=admin_id,
                status=SubscriptionStatus.expired,
                proxy_settings=ProxyTable().dict(no_obj=True),
            )
            limited_user = Subscription(
                uuid=uuid4(),
                admin_id=admin_id,
                status=SubscriptionStatus.limited,
                proxy_settings=ProxyTable().dict(no_obj=True),
            )
            session.add_all([expired_user, limited_user])
            await session.flush()

            tehran_tz = timezone(timedelta(hours=3, minutes=30))
            start = datetime(2026, 12, 10, 0, 0, 0, tzinfo=tehran_tz)
            end = datetime(2026, 12, 10, 3, 0, 0, tzinfo=tehran_tz)
            rows = [
                (active_user_id, datetime(2026, 12, 9, 23, 50, 0, tzinfo=tehran_tz)),
                (active_user_id, datetime(2026, 12, 10, 0, 10, 0, tzinfo=tehran_tz)),
                (active_user_id, datetime(2026, 12, 10, 0, 20, 0, tzinfo=tehran_tz)),
                (expired_user.uuid, datetime(2026, 12, 10, 0, 30, 0, tzinfo=tehran_tz)),
                (limited_user.uuid, datetime(2026, 12, 10, 1, 5, 0, tzinfo=tehran_tz)),
                (active_user_id, datetime(2026, 12, 10, 1, 10, 0, tzinfo=tehran_tz)),
                (active_user_id, datetime(2026, 12, 10, 3, 0, 0, tzinfo=tehran_tz)),
            ]
            for idx, (subscription_uuid, local_ts) in enumerate(rows):
                session.add(
                    NodeSubscriptionUsage(
                        created_at=local_ts.astimezone(UTC),
                        subscription_uuid=subscription_uuid,
                        node_id=node_id,
                        used_traffic=idx + 1,
                    )
                )
            await session.commit()

            online = await get_subscription_count_metric_stats(
                session,
                admins=[admin_username],
                start=start,
                end=end,
                period=Period.hour,
                metric=SubscriptionCountMetric.online,
            )
            expired = await get_subscription_count_metric_stats(
                session,
                admins=[admin_username],
                start=start,
                end=end,
                period=Period.hour,
                metric=SubscriptionCountMetric.expired,
            )
            limited = await get_subscription_count_metric_stats(
                session,
                admins=[admin_username],
                start=start,
                end=end,
                period=Period.hour,
                metric=SubscriptionCountMetric.limited,
            )

            assert isinstance(online, SubscriptionCountMetricStatsList)
            stats = online.stats[-1]
            assert [stat.period_start for stat in stats] == [
                datetime(2026, 12, 10, 0, 0, 0, tzinfo=tehran_tz),
                datetime(2026, 12, 10, 1, 0, 0, tzinfo=tehran_tz),
            ]
            assert [stat.count for stat in online.stats[-1]] == [2, 2]
            assert [stat.count for stat in expired.stats[-1]] == [1, 0]
            assert [stat.count for stat in limited.stats[-1]] == [0, 1]

    @pytest.mark.asyncio
    async def test_single_metric_responses_share_count_logic(self):
        async with TestSession() as session:
            admin_id, active_user_id, node_id = await setup_test_data(session)

            expired_user = Subscription(
                uuid=uuid4(),
                admin_id=admin_id,
                status=SubscriptionStatus.expired,
                proxy_settings=ProxyTable().dict(no_obj=True),
            )
            limited_user = Subscription(
                uuid=uuid4(),
                admin_id=admin_id,
                status=SubscriptionStatus.limited,
                proxy_settings=ProxyTable().dict(no_obj=True),
            )
            session.add_all([expired_user, limited_user])
            await session.flush()

            start = datetime(2026, 12, 12, 0, 0, 0, tzinfo=UTC)
            end = datetime(2026, 12, 12, 1, 0, 0, tzinfo=UTC)
            rows = [
                (active_user_id, start + timedelta(minutes=5)),
                (active_user_id, start + timedelta(minutes=15)),
                (expired_user.uuid, start + timedelta(minutes=25)),
                (limited_user.uuid, start + timedelta(minutes=35)),
            ]
            for idx, (subscription_uuid, created_at) in enumerate(rows):
                session.add(
                    NodeSubscriptionUsage(
                        created_at=created_at,
                        subscription_uuid=subscription_uuid,
                        node_id=node_id,
                        used_traffic=idx + 1,
                    )
                )
            await session.commit()

            online = await get_subscription_count_metric_stats(
                session,
                admins=None,
                start=start,
                end=end,
                period=Period.hour,
                metric=SubscriptionCountMetric.online,
            )
            expired = await get_subscription_count_metric_stats(
                session,
                admins=None,
                start=start,
                end=end,
                period=Period.hour,
                metric=SubscriptionCountMetric.expired,
            )
            limited = await get_subscription_count_metric_stats(
                session,
                admins=None,
                start=start,
                end=end,
                period=Period.hour,
                metric=SubscriptionCountMetric.limited,
            )

            assert isinstance(online, SubscriptionCountMetricStatsList)
            assert online.metric == SubscriptionCountMetric.online
            assert online.stats[-1][0].count == 3
            assert expired.metric == SubscriptionCountMetric.expired
            assert expired.stats[-1][0].count == 1
            assert limited.metric == SubscriptionCountMetric.limited
            assert limited.stats[-1][0].count == 1

    @pytest.mark.asyncio
    async def test_partial_first_bucket_is_excluded(self):
        async with TestSession() as session:
            _admin_id, subscription_uuid, node_id = await setup_test_data(session)

            tehran_tz = timezone(timedelta(hours=3, minutes=30))
            start = datetime(2026, 5, 9, 14, 2, 37, tzinfo=tehran_tz)
            end = datetime(2026, 5, 9, 18, 0, 0, tzinfo=tehran_tz)
            local_timestamps = [
                datetime(2026, 5, 9, 14, 10, 0, tzinfo=tehran_tz),
                datetime(2026, 5, 9, 15, 10, 0, tzinfo=tehran_tz),
                datetime(2026, 5, 9, 16, 10, 0, tzinfo=tehran_tz),
            ]
            for idx, local_ts in enumerate(local_timestamps):
                session.add(
                    NodeSubscriptionUsage(
                        created_at=local_ts.astimezone(UTC),
                        subscription_uuid=subscription_uuid,
                        node_id=node_id,
                        used_traffic=idx + 1,
                    )
                )
            await session.commit()

            result = await get_subscription_count_metric_stats(
                session,
                admins=None,
                start=start,
                end=end,
                period=Period.hour,
                metric=SubscriptionCountMetric.online,
            )

            stats = result.stats[-1]
            assert [stat.period_start for stat in stats] == [
                datetime(2026, 5, 9, 15, 0, 0, tzinfo=tehran_tz),
                datetime(2026, 5, 9, 16, 0, 0, tzinfo=tehran_tz),
            ]
            assert [stat.count for stat in stats] == [1, 1]

    @pytest.mark.asyncio
    async def test_node_grouping_node_filter_and_admin_filter(self):
        async with TestSession() as session:
            admin_id, subscription_uuid, node_id = await setup_test_data(session)
            admin_username = (await session.execute(select(Admin.username).where(Admin.id == admin_id))).scalar_one()

            admin_two = Admin(username=f"admin_counts_{uuid4().hex[:8]}", hashed_password="secret", role_id=3)
            node_two = Node(
                name=f"node_counts_{uuid4().hex[:8]}",
                address="127.0.0.1",
                port=8081,
                api_port=62051,
                server_ca="ca",
                api_key="key",
                core_config_id=None,
            )
            session.add_all([admin_two, node_two])
            await session.flush()

            other_user = Subscription(
                uuid=uuid4(),
                admin_id=admin_two.id,
                status=SubscriptionStatus.expired,
                proxy_settings=ProxyTable().dict(no_obj=True),
            )
            session.add(other_user)
            await session.flush()

            start = datetime(2026, 12, 11, 0, 0, 0, tzinfo=UTC)
            end = datetime(2026, 12, 11, 1, 0, 0, tzinfo=UTC)
            rows = [
                (subscription_uuid, node_id, start + timedelta(minutes=10)),
                (subscription_uuid, node_two.id, start + timedelta(minutes=20)),
                (other_user.uuid, node_id, start + timedelta(minutes=30)),
                (other_user.uuid, node_two.id, start + timedelta(minutes=40)),
            ]
            for idx, (row_user_id, row_node_id, created_at) in enumerate(rows):
                session.add(
                    NodeSubscriptionUsage(
                        created_at=created_at,
                        subscription_uuid=row_user_id,
                        node_id=row_node_id,
                        used_traffic=idx + 1,
                    )
                )
            await session.commit()

            grouped_online = await get_subscription_count_metric_stats(
                session,
                admins=None,
                start=start,
                end=end,
                period=Period.hour,
                metric=SubscriptionCountMetric.online,
                group_by_node=True,
            )
            assert grouped_online.stats[node_id][0].count == 2
            assert grouped_online.stats[node_two.id][0].count == 2

            admin_filtered_online = await get_subscription_count_metric_stats(
                session,
                admins=[admin_username],
                start=start,
                end=end,
                period=Period.hour,
                metric=SubscriptionCountMetric.online,
                group_by_node=True,
            )
            assert admin_filtered_online.stats[node_id][0].count == 1
            assert admin_filtered_online.stats[node_two.id][0].count == 1

            node_filtered_online = await get_subscription_count_metric_stats(
                session,
                admins=None,
                start=start,
                end=end,
                period=Period.hour,
                metric=SubscriptionCountMetric.online,
                node_id=node_id,
            )
            assert list(node_filtered_online.stats) == [node_id]
            assert node_filtered_online.stats[node_id][0].count == 2

    @pytest.mark.asyncio
    async def test_status_metrics_reject_node_scope(self):
        async with TestSession() as session:
            _admin_id, _user_id, node_id = await setup_test_data(session)

            start = datetime(2026, 12, 11, 0, 0, 0, tzinfo=UTC)
            end = datetime(2026, 12, 11, 1, 0, 0, tzinfo=UTC)

            with pytest.raises(ValueError, match="Only online subscription counts"):
                await get_subscription_count_metric_stats(
                    session,
                    admins=None,
                    start=start,
                    end=end,
                    period=Period.hour,
                    metric=SubscriptionCountMetric.expired,
                    node_id=node_id,
                )

            with pytest.raises(ValueError, match="Only online subscription counts"):
                await get_subscription_count_metric_stats(
                    session,
                    admins=None,
                    start=start,
                    end=end,
                    period=Period.hour,
                    metric=SubscriptionCountMetric.limited,
                    group_by_node=True,
                )
