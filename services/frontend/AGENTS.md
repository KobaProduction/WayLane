# WayLane Frontend — agent map

Source: https://github.com/KobaProduction/waylane (services/frontend/)

Reusable engineering guidance: https://github.com/ArthurKoba/ai-agent-workflow
Read the workflow AGENTS.md; load relevant frontend, Git and bootstrap skills only.

## Code and contracts
- `src/`: current React UI; Vue 3 migration is future development.
- `subscription-page/`: future Vue subscription interface.
- `package.json`, `bun.lock`: toolchain and dependencies.
- `scripts/verify.sh`: checks and build; CI runs the standard toolchain.
- Backend API authority: `KobaProduction/waylane/services/backend/`.
- `UPSTREAM.md` and `LICENSING.md`: source provenance and license scope.

Do not change production subscription links or gateway behavior without approval.
