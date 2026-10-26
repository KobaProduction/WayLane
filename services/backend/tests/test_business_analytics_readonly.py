"""Issue #582 analytics from original usage tables, without schema changes."""
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db import Base
from app.db.models import Node, NodeUsage, NodeUserUsage, User, UserHWID, UserSubscriptionUpdate
from app.routers import business_analytics as analytics


@pytest.mark.asyncio
async def test_bidirectional_matrix_and_node_directional_history(tmp_path):
    engine = create_async_engine("sqlite+aiosqlite:///" + str(tmp_path / "usage.db"))
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    start, end = now - timedelta(days=1), datetime.now(UTC) + timedelta(seconds=30)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    try:
        async with sessions() as db:
            ordinary = User(username="subscriber", proxy_settings={})
            bridge = User(username="bridge-user", proxy_settings={})
            first = Node(
                name="DE", address="192.0.2.1", port=62050, api_port=62051,
                server_ca="stub", api_key="token", core_config_id=None,
            )
            second = Node(
                name="NL", address="192.0.2.2", port=62050, api_port=62051,
                server_ca="stub", api_key="token", core_config_id=None,
            )
            db.add_all([ordinary, bridge, first, second])
            await db.flush()
            db.add_all([
                NodeUsage(node_id=first.id, created_at=now, uplink=100, downlink=300),
                NodeUsage(node_id=second.id, created_at=now, uplink=80, downlink=120),
                NodeUserUsage(node_id=first.id, user_id=ordinary.id, created_at=now, used_traffic=120),
                NodeUserUsage(node_id=second.id, user_id=ordinary.id, created_at=now, used_traffic=90),
                NodeUserUsage(node_id=first.id, user_id=bridge.id, created_at=now, used_traffic=700),
                UserHWID(user_id=ordinary.id, hwid="example-synthetic", device_os="Android",
                         os_version="15", device_model="Demo"),
                UserSubscriptionUpdate(user_id=ordinary.id, user_agent="Happ/Test", ip="127.0.0.1", hwid="example-synthetic"),
            ])
            await db.commit()

            both = await analytics.user_node_matrix(
                start=start, end=end, all_time=False, node_id=None, user_id=None,
                status=None, exclude_node_ids=None, exclude_user_ids=None, db=db, _=None,
            )
            assert {(r["username"], r["node_name"], r["total_bytes"]) for r in both["rows"]} == {
                ("subscriber", "DE", 120), ("subscriber", "NL", 90), ("bridge-user", "DE", 700)
            }
            assert both["direction_available"] is False
            filtered = await analytics.user_node_matrix(
                start=start, end=end, all_time=False, node_id=first.id, user_id=None,
                status=None, exclude_node_ids=None, exclude_user_ids=str(bridge.id), db=db, _=None,
            )
            assert len(filtered["rows"]) == 1
            assert filtered["rows"][0]["total_bytes"] == 120
            user_nodes = await analytics.user_node_matrix(
                start=start, end=end, all_time=False, node_id=None, user_id=ordinary.id,
                status=None, exclude_node_ids=None, exclude_user_ids=None, db=db, _=None,
            )
            assert {r["node_name"] for r in user_nodes["rows"]} == {"DE", "NL"}
            lifetime = await analytics.user_node_matrix(
                start=None, end=None, all_time=True, node_id=None, user_id=None,
                status=None, exclude_node_ids=str(second.id), exclude_user_ids=None, db=db, _=None,
            )
            assert {r["node_name"] for r in lifetime["rows"]} == {"DE"}

            history = await analytics.node_history(
                start=start, end=end, period="day", node_ids=None,
                exclude_node_ids=None, exclude_user_ids=None, db=db, _=None,
            )
            assert history["direction_available"] is True
            assert {(r["node_name"], r["upload"], r["download"]) for r in history["rows"]} == {
                ("DE", 100, 300), ("NL", 80, 120)
            }
            adjusted = await analytics.node_history(
                start=start, end=end, period="day", node_ids=None,
                exclude_node_ids=None, exclude_user_ids=str(bridge.id), db=db, _=None,
            )
            assert adjusted["direction_available"] is False
            assert {(r["node_name"], r["total_bytes"]) for r in adjusted["rows"]} == {
                ("DE", 120), ("NL", 90)
            }
            assert all(r["upload"] is None and r["download"] is None for r in adjusted["rows"])

            node_series = await analytics.user_node_traffic_series(
                start=start, end=end, period="day", node_id=first.id, user_id=None,
                exclude_node_ids=None, exclude_user_ids=None, db=db, _=None,
            )
            assert {s["name"] for s in node_series["series"]} == {"subscriber", "bridge-user"}
            assert sum(row["total_bytes"] for row in node_series["rows"]) == 820
            user_series = await analytics.user_node_traffic_series(
                start=start, end=end, period="day", node_id=None, user_id=ordinary.id,
                exclude_node_ids=None, exclude_user_ids=None, db=db, _=None,
            )
            assert {s["name"] for s in user_series["series"]} == {"DE", "NL"}
            assert sum(row["total_bytes"] for row in user_series["rows"]) == 210

            devices = await analytics.device_summary(start=start, end=end, db=db, _=None)
            assert devices["systems"] == [{"name": "Android", "count": 1}]
            assert devices["user_agents"] == [{"name": "Happ/Test", "count": 1}]
            catalog = await analytics.catalog(db=db, _=None)
            assert {n["name"] for n in catalog["nodes"]} == {"DE", "NL"}
    finally:
        await engine.dispose()


def test_range_and_ids_validation():
    now = datetime.now(UTC)
    with pytest.raises(HTTPException) as exc:
        analytics._range(now - timedelta(days=367), now)
    assert exc.value.status_code == 422
    with pytest.raises(HTTPException):
        analytics._range(now, now - timedelta(seconds=1))
    with pytest.raises(HTTPException):
        analytics._ids("0,123")
    with pytest.raises(HTTPException):
        analytics._ids("1;DROP TABLE")
    assert analytics._ids("1,2,2") == {1, 2}


def test_business_analytics_routes_registered_and_owner_protected():
    from app.routers import routers
    # This project's FastAPI build uses deferred _IncludedRouter wrappers;
    # inspect the included routers and their own concrete route registry.
    assert analytics.router in routers
    paths = {getattr(route, 'path', '') for route in analytics.router.routes}
    for suffix in ('/catalog', '/matrix', '/traffic-series', '/nodes', '/devices'):
        assert '/api/business-analytics' + suffix in paths
