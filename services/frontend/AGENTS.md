# WayLane Frontend — agent map

Source: https://github.com/KobaProduction/WayLane/tree/main/services/frontend

Reusable engineering guidance: https://github.com/ArthurKoba/ai-agent-workflow
Read the workflow AGENTS.md; load relevant frontend, Git and bootstrap skills only.

## Code and contracts
- `src/`: current React UI; Vue 3 migration is future development.
- `subscription-page/`: future Vue subscription interface.
- `package.json`, `bun.lock`: toolchain and dependencies.
- `scripts/verify.sh`: checks and build; CI runs the standard toolchain.
- Backend API authority: `services/backend/` in this monorepo.
- `UPSTREAM.md` and `LICENSING.md`: source provenance and license scope.

Do not change production subscription links or gateway behavior without approval.
