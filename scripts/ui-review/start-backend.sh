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
# Local-only synthetic Fernet vault, separate from every Coolify/production key.
# It remains in the ignored isolated fixture, never in Git or command output.
VAULT_FILE="$ROOT/.runtime/ui-review/vault.key"
if [[ ! -e "$VAULT_FILE" ]]; then
  .venv/bin/python - "$VAULT_FILE" <<'PYVAULT'
import os, sys
from cryptography.fernet import Fernet
fd=os.open(sys.argv[1],os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
with os.fdopen(fd,'w',encoding='ascii') as output:
    output.write(Fernet.generate_key().decode('ascii'))
PYVAULT
fi
[[ -f "$VAULT_FILE" && ! -L "$VAULT_FILE" ]] || exit 1
export WAYLANE_TLS_SECRET_KEY="$(< "$VAULT_FILE")"
# Do not start scheduler/background jobs in a disposable UI fixture.
exec .venv/bin/uvicorn app.app_factory:create_app --factory --host 127.0.0.1 --port 8321 --no-access-log --lifespan off
