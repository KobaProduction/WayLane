"""Create a tiny, disposable WayLane UI dataset in the isolated SQLite fixture.

Never provision owners or test nodes against a real WayLane database.
Only the dedicated .runtime/ui-review/waylane-ui-review.sqlite3 path is allowed.
"""

import asyncio
import json
import os
import secrets
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from sqlalchemy import func, insert, select

ROOT = Path(__file__).resolve().parents[2]
RUNTIME = ROOT / ".runtime/ui-review"
DATABASE = RUNTIME / "waylane-ui-review.sqlite3"
CREDENTIALS = RUNTIME / "owner-credentials.json"
EXPECTED_DSN = f"sqlite+aiosqlite:///{DATABASE}"

if os.environ.get("WAYLANE_UI_REVIEW_BOOTSTRAP") != "yes":
    raise SystemExit("Isolated UI review bootstrap must be explicitly enabled")
if os.environ.get("SQLALCHEMY_DATABASE_URL") != EXPECTED_DSN:
    raise SystemExit("Refusing non-isolated UI review database URL")
if RUNTIME.is_symlink() or DATABASE.is_symlink() or CREDENTIALS.is_symlink():
    raise SystemExit("Symlinked UI review paths are not permitted")

from app.db.base import Base, SessionLocal, engine  # noqa: E402
from app.db.models import (  # noqa: E402
    Admin, AdminRole, JWT, Settings, System,
    node_agent_events, node_agent_registrations,
)
from app.models.admin import hash_password  # noqa: E402


async def main() -> None:
    RUNTIME.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(RUNTIME, 0o700)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    async with SessionLocal() as db:
        if await db.scalar(select(func.count()).select_from(Admin)):
            if not CREDENTIALS.is_file():
                raise SystemExit("Existing isolated admin has no credential file; refusing to replace credentials")
            print("Existing isolated UI review fixture retained")
            return

        if CREDENTIALS.exists():
            raise SystemExit("Existing credential file with empty database; manual fixture diagnosis required")
        role = AdminRole(name="UI Review Owner", is_owner=True)
        db.add(role)
        await db.flush()
        username = "ui_review_owner"
        password = "UiReview42!_" + secrets.token_urlsafe(16)
        db.add(Admin(username=username, hashed_password=await hash_password(password), role_id=role.id))
        db.add(JWT(secret_key=secrets.token_hex(32)))
        db.add(System())
        db.add(Settings(
            webhook={"enable": False, "webhooks": [], "days_left": [], "usage_percent": [], "timeout": 30, "recurrent": 3},
            notification_enable={}, subscription={}, hwid={}, general={},
        ))

        now = datetime.now(UTC)
        for name, status, capabilities in (
            ("test-node-alpha", "approved", ["docker.rootless.inspect", "xray.protobuf.binary"]),
            ("test-node-beta", "pending", []),
        ):
            node_uuid = str(uuid4())
            await db.execute(insert(node_agent_registrations).values(
                node_uuid=node_uuid,
                secret_hash="0" * 64,
                invitation_id=None,
                display_name=name,
                status=status,
                agent_version="0.1.0",
                capabilities=capabilities,
                created_at=now,
                last_seen_at=now if status == "approved" else None,
            ))
            if status == "approved":
                for index, (kind, event_status, core_id, generation) in enumerate((
                    ("connected", "connected", None, None),
                    ("xray_result", "running", "xray/default", 1),
                    ("node_action_report", "pending", "node/runtime", 2),
                    ("node_action_report", "error", "node/runtime", 2),
                    ("disconnected", "disconnected", None, None),
                    ("connected", "connected", None, None),
                )):
                    await db.execute(insert(node_agent_events).values(
                        event_uuid=str(uuid4()),
                        node_uuid=node_uuid,
                        created_at=now - timedelta(minutes=7 - index),
                        kind=kind,
                        status=event_status,
                        core_id=core_id,
                        generation=generation,
                    ))

        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(CREDENTIALS, flags, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            json.dump({"username": username, "password": password}, output)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        await db.commit()
        print("Isolated UI review seeded: one synthetic owner, two nodes, six events")

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
