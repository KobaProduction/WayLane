"""Client-side Xray balancers retain each member's own outbound credentials."""

import asyncio
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from sqlalchemy import insert, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db import Base
from app.db.host_balancer import host_balancers
from app.models.subscription import SubscriptionInboundData, TCPTransportConfig, TLSConfig
from app.routers.host_balancer import BalancerInput
from app.subscription import share
from app.subscription.xray import XrayConfiguration


def make_inbound(remark: str, address: str) -> SubscriptionInboundData:
    return SubscriptionInboundData(
        remark=remark, inbound_tag="vless-main", protocol="vless",
        address=[address], port=[443], network="tcp",
        tls_config=TLSConfig(tls="none"),
        transport_config=TCPTransportConfig(path="", host=[]),
        priority=0,
    )


TEMPLATE = {
    "outbounds": [{"tag": "DIRECT", "protocol": "freedom"}],
    "routing": {"rules": [{"type": "field", "domain": ["domain:internal.example"], "outboundTag": "DIRECT"}]},
}


@pytest.mark.parametrize("strategy,observer", [
    ("roundRobin", None),
    ("random", None),
    ("leastPing", "observatory"),
    ("leastLoad", "burstObservatory"),
])
def test_xray_balancer_routing_and_observatory(strategy, observer):
    config = XrayConfiguration(xray_template_content=json.dumps(TEMPLATE))
    group_members = [
        ("one.example", make_inbound("One", "one.example"), {"id": "00000000-0000-0000-0000-000000000001"}),
        ("two.example", make_inbound("Two", "two.example"), {"id": "00000000-0000-0000-0000-000000000002"}),
    ]
    group_members = [(address, inbound.model_copy(update={"port": 443}), settings)
                     for address, inbound, settings in group_members]
    assert config.add_balancer(
        balancer_id=42, name="Europe", members=group_members,
        strategy=strategy, probe_interval=90,
    )
    documents = json.loads(config.render())
    assert len(documents) == 1
    document = documents[0]
    members = [out for out in document["outbounds"] if out["tag"].startswith("wlb-42-member-")]
    assert {out["tag"] for out in members} == {"wlb-42-member-0", "wlb-42-member-1"}
    assert members[0]["settings"]["vnext"][0]["address"] == "one.example"
    assert members[1]["settings"]["vnext"][0]["address"] == "two.example"
    assert members[0]["settings"]["vnext"][0]["users"][0]["id"] != members[1]["settings"]["vnext"][0]["users"][0]["id"]
    assert document["routing"]["balancers"] == [{
        "tag": "wlb-42", "selector": ["wlb-42-member-"], "strategy": {"type": strategy},
    }]
    assert document["routing"]["rules"][-1]["balancerTag"] == "wlb-42"
    assert document["routing"]["rules"][0]["outboundTag"] == "DIRECT"
    assert (observer in document) if observer else ("observatory" not in document and "burstObservatory" not in document)
    if observer == "observatory":
        assert document[observer]["probeInterval"] == "90s"
    if observer == "burstObservatory":
        assert document[observer]["pingConfig"]["interval"] == "90s"


@pytest.mark.parametrize("invalid", [
    {"name": "", "host_ids": [1, 2]},
    {"name": "Empty", "host_ids": [1]},
    {"name": "Dupe", "host_ids": [1, 1]},
    {"name": "Negative", "host_ids": [-1, 2]},
    {"name": "Probe", "host_ids": [1, 2], "probe_interval": 1},
    {"name": "Invalid", "host_ids": [1, 2], "strategy": "unknown"},
])
def test_balancer_rejects_invalid_inputs(invalid):
    with pytest.raises(ValidationError):
        BalancerInput.model_validate(invalid)


def test_xray_subscriptions_include_authorized_balancer_and_hide_individual_hosts(monkeypatch, tmp_path):
    db = create_async_engine("sqlite+aiosqlite:///" + str(tmp_path / "balancer.db"))
    sessions = async_sessionmaker(db, expire_on_commit=False)
    hosts = {1: make_inbound("One", "one.example"), 2: make_inbound("Two", "two.example")}
    for host in hosts.values():
        host.status = []
    @asynccontextmanager
    async def test_db():
        async with sessions() as session:
            yield session

    async def load_hosts():
        return hosts

    monkeypatch.setattr(share, "GetDB", test_db) if hasattr(share, "GetDB") else None
    from app.core import hosts as host_module
    monkeypatch.setattr(host_module.host_manager, "get_hosts", load_hosts)
    user = SimpleNamespace(
        id=1, status="active", inbounds=["vless-main"],
        proxy_settings=SimpleNamespace(dict=lambda: {
            "vless": {"id": "00000000-0000-0000-0000-000000000001"}
        }),
    )

    async def exercise():
        async with db.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with sessions() as session:
            await session.execute(insert(host_balancers).values(
                name="Balanced", host_ids=[1, 2], strategy="roundRobin",
                probe_interval=60, hide_members=True, is_disabled=False,
            ))
            await session.commit()
        conf = XrayConfiguration(xray_template_content=json.dumps(TEMPLATE))
        rendered = await share.process_inbounds_and_tags(
            user, {}, conf, {}, custom_variables=None,
        )
        entries = json.loads(rendered)
        assert len(entries) == 1
        assert entries[0]["remarks"] == "Balanced"
        assert len([o for o in entries[0]["outbounds"] if o["tag"].startswith("wlb-1-member-")]) == 2
        # No group if fewer than two members are authorized by the user.
        user.inbounds = []
        conf2 = XrayConfiguration(xray_template_content=json.dumps(TEMPLATE))
        assert json.loads(await share.process_inbounds_and_tags(user, {}, conf2, {})) == []
        await db.dispose()

    monkeypatch.setattr("app.db.GetDB", test_db)
    asyncio.run(exercise())


def test_balancer_crud_and_member_existence_validation(tmp_path):
    """API endpoints persist a named group and reject missing member IDs."""
    from fastapi import HTTPException

    from app.db.models import ProxyHost
    from app.routers.host_balancer import (
        _check_members, create_balancer, delete_balancer, list_balancers,
        update_balancer,
    )

    async def exercise():
        engine = create_async_engine("sqlite+aiosqlite:///" + str(tmp_path / "crud.db"))
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        async with sessions() as session:
            session.add_all([
                ProxyHost(remark="A", address={"one.example"}, port=443, path=None, priority=0, allowinsecure=False, alpn=[], status=[]),
                ProxyHost(remark="B", address={"two.example"}, port=443, path=None, priority=1, allowinsecure=False, alpn=[], status=[]),
            ])
            await session.commit()
            ids = (await session.scalars(select(ProxyHost.id).order_by(ProxyHost.id))).all()
            assert len(ids) == 2
            with pytest.raises(HTTPException) as error:
                await _check_members(session, [ids[0], max(ids) + 100])
            assert error.value.status_code == 422
            created = await create_balancer(
                BalancerInput(name="Xray group", host_ids=ids), session, None,
            )
            assert created.id > 0
            assert created.strategy == "roundRobin"
            rows = await list_balancers(session, None)
            assert len(rows) == 1
            updated = await update_balancer(
                created.id, BalancerInput(
                    name="Xray group", host_ids=ids, strategy="leastPing",
                    hide_members=True, probe_interval=80,
                ), session, None,
            )
            assert updated.strategy == "leastPing"
            rows = await list_balancers(session, None)
            assert rows[0]["hide_members"] is True
            await delete_balancer(created.id, session, None)
            assert await list_balancers(session, None) == []
        await engine.dispose()

    asyncio.run(exercise())


def test_xray_balancer_routes_before_template_catchall_but_after_specific_rules():
    template = {
        "outbounds": [{"tag": "DIRECT", "protocol": "freedom"}],
        "routing": {"rules": [
            {"type": "field", "domain": ["domain:internal.example"], "outboundTag": "DIRECT"},
            {"type": "field", "network": "tcp,udp", "outboundTag": "DIRECT"},
        ]},
    }
    config = XrayConfiguration(xray_template_content=json.dumps(template))
    members = [
        ("one.example", make_inbound("One", "one.example"), {"id": "00000000-0000-0000-0000-000000000001"}),
        ("two.example", make_inbound("Two", "two.example"), {"id": "00000000-0000-0000-0000-000000000002"}),
    ]
    assert config.add_balancer(
        balancer_id=8, name="Traffic", members=members, strategy="roundRobin"
    )
    rules = json.loads(config.render())[0]["routing"]["rules"]
    assert rules[0]["outboundTag"] == "DIRECT"
    assert rules[1]["balancerTag"] == "wlb-8"
    assert rules[2]["outboundTag"] == "DIRECT"


def test_xray_balancer_does_not_hide_members_when_it_cannot_build(monkeypatch, tmp_path):
    db = create_async_engine("sqlite+aiosqlite:///" + str(tmp_path / "invalid-members.db"))
    sessions = async_sessionmaker(db, expire_on_commit=False)
    hosts = {1: make_inbound("One", "one.example"), 2: make_inbound("Two", "two.example")}

    @asynccontextmanager
    async def test_db():
        async with sessions() as session:
            yield session

    async def load_hosts():
        return hosts

    monkeypatch.setattr("app.db.GetDB", test_db)
    from app.core import hosts as host_module

    monkeypatch.setattr(host_module.host_manager, "get_hosts", load_hosts)

    # A complete group with two host entries still fails if one protocol
    # does not have an outbound handler. Both individual entries must survive.
    hosts[1].protocol = "unsupported"
    user = SimpleNamespace(
        id=2, status="active", inbounds=["vless-main"],
        proxy_settings=SimpleNamespace(dict=lambda: {
            "vless": {"id": "00000000-0000-0000-0000-000000000001"},
            "unsupported": {"id": "00000000-0000-0000-0000-000000000002"},
        }),
    )

    async def exercise():
        async with db.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with sessions() as session:
            await session.execute(insert(host_balancers).values(
                name="Group", host_ids=[1, 2], strategy="roundRobin",
                probe_interval=60, hide_members=True, is_disabled=False,
            ))
            await session.commit()
        conf = XrayConfiguration(xray_template_content=json.dumps(TEMPLATE))
        result = json.loads(await share.process_inbounds_and_tags(user, {}, conf, {}))
        assert len(result) == 1
        assert result[0]["remarks"] == "Two"
        await db.dispose()

    asyncio.run(exercise())


def test_balancer_fragment_dialers_have_unique_tags():
    conf = XrayConfiguration(xray_template_content=json.dumps(TEMPLATE))
    members = []
    for i in range(2):
        inbound = make_inbound("Fragment " + str(i), "host" + str(i) + ".example")
        inbound.port = 443
        inbound.fragment_settings = {"xray": {"packets": "tlshello", "length": "100-200", "interval": "3-7"}}
        members.append(("host" + str(i) + ".example", inbound, {"id": "00000000-0000-0000-0000-000000000001"}))
    assert conf.add_balancer(balancer_id=25, name="Fragments", members=members, strategy="random")
    document = json.loads(conf.render())[0]
    outbounds = {out["tag"]: out for out in document["outbounds"]}
    assert len(outbounds) == len(document["outbounds"])
    for i in range(2):
        sockopt = outbounds["wlb-25-member-" + str(i)]["streamSettings"]["sockopt"]
        assert sockopt["dialerProxy"] == "wlb-25-aux-" + str(i) + "-0"
        assert outbounds[sockopt["dialerProxy"]]["protocol"] == "freedom"
