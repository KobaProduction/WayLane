"""UUID-first control events never expose raw Xray/Agent credentials."""
import asyncio
from uuid import UUID

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.base import Base
from app.node_events import append_node_event
from app.routers.node_registry import create_classic_node, list_node_control_events


def test_sanitized_wss_lifecycle_events_and_bounded_retention(tmp_path, monkeypatch):
    from app import node_events

    async def run():
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/events.db")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        monkeypatch.setattr(node_events, "MAX_EVENTS_PER_NODE", 3)
        try:
            async with factory() as db:
                agent = await create_classic_node(db=db, _=object())
                uid = UUID(agent["node_uuid"])
                for kind, status in [
                    ("connected", "connected"),
                    ("xray_result", "running"),
                    ("xray_result", "error"),
                    ("disconnected", "disconnected"),
                ]:
                    await append_node_event(db, str(uid), kind=kind, status=status,
                                            core_id="xray/default" if kind == "xray_result" else None,
                                            generation=1 if kind == "xray_result" else None)
                await db.commit()
                response = await list_node_control_events(uid, db=db, _=object(), limit=200)
                assert len(response["events"]) == 3
                assert all("secret" not in row and "config" not in row
                           and "token" not in row for row in response["events"])
                assert agent["node_token"] not in str(response)
                with pytest.raises(ValueError):
                    await append_node_event(db, str(uid), kind="arbitrary-secret", status="running")
        finally:
            await engine.dispose()
    asyncio.run(run())
