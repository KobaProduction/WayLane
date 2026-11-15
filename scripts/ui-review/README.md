# Isolated local WayLane UI review

This is a browser-test fixture for the **real React/Vite frontend and FastAPI
Backend**, not a replacement for staging or a VPN/Core network acceptance test.
It never connects to a production database, Coolify or a real Node, and it
never starts the Agent, rootless Docker or a VPN Core. This keeps interface
iteration independent from CI/CD and the VPN infrastructure.

**Prerequisites:** existing locked Bun/node dependencies under
`services/frontend/node_modules`, Python/uv environment under
`services/backend/.venv`; the repo's supported toolchains. Two terminals
in the same authorised workspace:

1. From the repository root run `scripts/ui-review/start-backend.sh`.
   It verifies the **exact** private SQLite path inside ignored
   `.runtime/ui-review`, seeds an owner, two synthetic Node states and six
   example WSS events, and maintains a private, synthetic Fernet vault key in
   `.runtime/ui-review/vault.key` for local write-only credential tests.
   It then runs FastAPI on `127.0.0.1:8321` with its
   lifespan disabled (so no scheduler, workers or periodic jobs).
2. From the repository root run `scripts/ui-review/start-frontend.sh`.
   Vite serves the interface on port `5174` with hot reloading. Only when
   `WAYLANE_UI_REVIEW_API` exactly equals the local loopback Backend does Vite
   proxy `/api/` and `/sub/` to the fixture; never proxy live credentials or
   a production API through this dev server.
3. Open that Vite page using the already-authorised local Playwright browser
   session and log in with the **synthetic** credentials from
   `.runtime/ui-review/owner-credentials.json` (`0600`, ignored by Git).
   Never print or commit those credentials. Exercise `/nodes`, its
   **Add node** dialog, `/nodes/logs`, direct navigation and mobile viewport.
4. Stop each foreground server when finished. Keep the ignored test database
   only if follow-up UI work needs the same scenario. Neither command deletes
   or resets any existing database, credentials or live server resource.

**Acceptance scope:** Node creation and invitations are real API operations
only in the isolated SQLite fixture; the local Node entries and WSS events
are synthetic. No real tunnelling, Agent update, registry pull or Core
connectivity is asserted. Such acceptance remains a separate runtime gate.
