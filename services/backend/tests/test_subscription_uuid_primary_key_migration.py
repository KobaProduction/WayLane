"""In-place numeric-to-UUID migration retains dependent rows and queued events."""

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

BACKEND = Path(__file__).resolve().parents[1]
CHILD_TABLES = (
    "next_plans",
    "node_subscription_usages",
    "notification_reminders",
    "subscription_hwids",
    "subscription_updates",
    "subscription_usage_logs",
    "subscriptions_groups_association",
    "subscription_device_events",
)


def _upgrade(database: Path, revision: str) -> None:
    env = {
        **os.environ,
        "SQLALCHEMY_DATABASE_URL": f"sqlite+aiosqlite:///{database}",
        "SQLALCHEMY_MIGRATION_DATABASE_URL": f"sqlite+aiosqlite:///{database}",
    }
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", revision],
        cwd=BACKEND,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=90,
    )
    assert result.returncode == 0, result.stdout + result.stderr[-4000:]


def test_uuid_primary_key_migration_keeps_devices_traffic_and_queued_webhooks(tmp_path):
    database = tmp_path / "subscription-uuid-migration.sqlite3"
    _upgrade(database, "a1c75e8db902")

    canonical = uuid4()
    old_id = 4711
    event_id = str(uuid4())
    created_at = "2026-11-08 00:00:00"
    with sqlite3.connect(database) as db:
        db.execute(
            """INSERT INTO subscriptions
               (id, uuid, status, used_traffic, entitlement, entitlement_revision,
                policy_revision, access_generation, access_nonce)
               VALUES (?, ?, 'active', 150, 'active', 1, 2, 3, ?)""",
            (old_id, canonical.hex, b"0123456789abcdef"),
        )
        db.execute("INSERT INTO groups(id, name) VALUES (77, 'migration-group')")
        db.execute("INSERT INTO next_plans(subscription_id, data_limit) VALUES (?, 1000)", (old_id,))
        db.execute(
            "INSERT INTO node_subscription_usages(subscription_id, created_at, used_traffic) VALUES (?, ?, 31)",
            (old_id, created_at),
        )
        db.execute(
            "INSERT INTO notification_reminders(subscription_id, type, created_at, threshold) VALUES (?, 'data_usage', ?, 80)",
            (old_id, created_at),
        )
        db.execute(
            "INSERT INTO subscription_hwids(subscription_id, hwid, created_at, last_used_at) VALUES (?, 'device-a', ?, ?)",
            (old_id, created_at, created_at),
        )
        db.execute(
            "INSERT INTO subscription_updates(subscription_id, created_at, user_agent) VALUES (?, ?, 'Happ/1')",
            (old_id, created_at),
        )
        db.execute(
            "INSERT INTO subscription_usage_logs(subscription_id, used_traffic_at_reset, reset_at) VALUES (?, 66, ?)",
            (old_id, created_at),
        )
        db.execute("INSERT INTO subscriptions_groups_association(subscription_id, groups_id) VALUES (?, 77)", (old_id,))
        db.execute(
            "INSERT INTO subscription_device_events(id, event_type, subscription_id, payload) VALUES (?, 'device.added', ?, ?)",
            (event_id, old_id, json.dumps({
                "id": event_id,
                "type": "device.added",
                "user_id": old_id,
                "username": "retired-username",
                "details": {"device_os": "Android"},
            })),
        )

    _upgrade(database, "head")
    with sqlite3.connect(database) as db:
        primary_key = {row[1] for row in db.execute("PRAGMA table_info(subscriptions)") if row[5]}
        assert primary_key == {"uuid"}
        assert "id" not in {row[1] for row in db.execute("PRAGMA table_info(subscriptions)")}
        for table in CHILD_TABLES:
            assert "subscription_id" not in {item[1] for item in db.execute(f"PRAGMA table_info({table})")}
        assert db.execute("SELECT uuid FROM subscriptions").fetchone()[0] == canonical.hex
        for table in CHILD_TABLES:
            row = db.execute(f"SELECT subscription_uuid FROM {table}").fetchone()
            assert row is not None and row[0] == canonical.hex, table
            assert db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 1
            if table != "subscription_device_events":
                foreign_keys = list(db.execute(f"PRAGMA foreign_key_list({table})"))
                assert any(fk[2] == "subscriptions" and fk[3] == "subscription_uuid" and fk[4] == "uuid" for fk in foreign_keys), table
        payload = json.loads(db.execute("SELECT payload FROM subscription_device_events").fetchone()[0])
        assert payload["subscription_uuid"] == str(canonical)
        assert payload["type"] == "device.added"
        assert payload["details"]["device_os"] == "Android"
        assert set(payload).isdisjoint({"user_id", "username", "hwid", "subscription_id"})
