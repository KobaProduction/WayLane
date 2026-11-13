# WayLane Frontend

Repository owner: **Koba Production**; source is `KobaProduction/waylane/services/frontend/` in the sole WayLane monorepo. This directory contains the admin dashboard and future subscriber landing page.

Current source: the upstream React/TypeScript dashboard (transitional).
Target: Vue 3 with separate admin and subscription-page routes. Do not claim
Vue has been implemented yet.

### Initial build
- Install compatible Bun/Node toolchain.
- Run `bun install --frozen-lockfile` and `VITE_BASE_API=/ bun run build`.
- Build frontend from `services/frontend/` inside the single `KobaProduction/waylane` repository. No additional source checkout is required.
- This directory builds its own `frontend` image inside the same monorepo; the backend image is built independently from `services/backend/`.
- Coolify DEV deploys **Backend**, **Frontend**, **Dependencies** (PostgreSQL) and
  **Node** as four independently owned Compose resources in the `WayLane`
  project. Backend/Frontend builds use `main` from the same monorepo; Node
  deployment remains manual and preserves its own Agent/Node lifecycle.
- Saved Xray Core Editor profiles can be explicitly applied to autonomous
  Nodes through UUID-first Node Registry and authenticated WSS. TLS bindings,
  Node lifecycle events and commercial subscription admission use the same
  control plane; the old numeric PasarGuard Node API and React consumers are
  retired in Issue #21.
- `/nodes` renders Classic/Discovery registration, Core assignment, per-Node
  Xray/AWG desired state and read-only cumulative traffic diagnostics.
  `/nodes/logs` displays sanitized WSS control events rather than raw old RPC
  streaming logs. The statistics page separates historical numeric Node
  records from autonomous UUID-first counters and heartbeats. Individual
  outbound RTT remains unverified: Core Editor displays assignment/runtime
  state instead of misrepresenting it as a network latency measurement.

The existing subscription-page HTML lives in backend until Vue replacement is
built, to avoid breaking client links. Commercial subscriptions stay in
Intens vpn-core. For attribution and legal terms see UPSTREAM.md and LICENSE.


## AI workflow and verification

Read `AGENTS.md` and the task-specific rules from
[AI Agent Workflow](https://github.com/ArthurKoba/ai-agent-workflow).
On a new checkout enable the tracked hooks with
`git config --local core.hooksPath .githooks`; run `./scripts/verify.sh`
before attempting a push. CI runs the same repository-specific gate.

**Bootstrap limitation:** Passing these checks does not prove runtime
deployment compatibility, or complete the eventual Vue/HA migration.

Licensing note: GitHub root MIT `LICENSE` does not replace the upstream copyleft license applicable to derived code. See `LICENSING.md` and `LICENSE.UPSTREAM`.

**Memory-constrained development:** in a container limited to 2 GiB, run
`WAYLANE_LOW_MEMORY_TYPECHECK=1 ./scripts/verify.sh`. This performs a
pinned native TypeScript 7 preview check (single-threaded) plus the actual
Vite build. It does **not** claim parity with the repository's TypeScript 5.6
compiler: GitHub Actions runs the unchanged full TypeScript 5.6 check.
