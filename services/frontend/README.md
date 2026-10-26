# WayLane Frontend

Repository owner: **Koba Production**; original integration: Intens. Separate frontend repository for WayLane admin dashboard and
future subscriber landing page.

Current source: the upstream React/TypeScript dashboard (transitional).
Target: Vue 3 with separate admin and subscription-page routes. Do not claim
Vue has been implemented yet.

### Initial build
- Install compatible Bun/Node toolchain.
- Run `bun install --frozen-lockfile` and `VITE_BASE_API=/ bun run build`.
- Publish `build/` as a CI artifact and supply it to the `KobaProduction/waylane-backend` packaging flow. No additional source checkout is required.
- The backend serves compiled assets with its existing Python integration shim.
- A separate web-service container is **not necessary** for the first migration.

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
