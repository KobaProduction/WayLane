# WayLane Backend — agent map

Current source: https://github.com/KobaProduction/waylane/tree/main/services/backend

Reusable engineering guidance: https://github.com/ArthurKoba/ai-agent-workflow
Read the upstream workflow AGENTS.md and load only task-relevant skills.

## Code and contracts
- `app/`: FastAPI API, persistence and node orchestration.
- `app/templates/subscription/`: current subscription HTML.
- `dashboard/__init__.py`: adapter for the separately built frontend.
- `scripts/verify.sh`: backend checks, also used by GitHub Actions.
- `UPSTREAM.md` and `LICENSING.md`: source provenance and license scope.
- Intens `vpn-core` owns commercial subscriptions and the current public gateway.
- Shared Intens contracts: `intens-cloud/infrastructure`.

Preserve API and migration compatibility; do not change production without authorization.

WayLane Node Agent discovery API, migrations and IP observation registry belong to
`app/routers/node_discovery.py`. Registration must not silently mutate
legacy `nodes`, hosts, certificates or subscription endpoints.
