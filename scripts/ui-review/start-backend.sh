#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT/services/backend"
export SQLALCHEMY_DATABASE_URL="sqlite+aiosqlite:///$ROOT/.runtime/ui-review/waylane-ui-review.sqlite3"
export NATS_ENABLED=false
export TESTING=true
export DOCS=false
export OTEL_SDK_DISABLED=true
WAYLANE_UI_REVIEW_BOOTSTRAP=yes PYTHONPATH=. .venv/bin/python "$ROOT/scripts/ui-review/seed.py"
# Do not start scheduler/background jobs in a disposable UI fixture.
exec .venv/bin/uvicorn app.app_factory:create_app --factory --host 127.0.0.1 --port 8321 --no-access-log --lifespan off
