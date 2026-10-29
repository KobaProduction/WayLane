Source: https://github.com/PasarGuard/panel at commit
`5eb8628cfe6db233ed2a6bbaf53f0ed497e797f9`, retrieved 2026-10-24.
AGPL-3.0 license and upstream copyright notices remain intact in `LICENSE.UPSTREAM`.
The original dashboard React/TypeScript source is now in
`services/frontend/`; the Python dashboard serving shim remains here.
No source ownership is transferred by this split. Preserve original attribution.
A separate repository PasarGuard/subscription-template has no root LICENSE in the
snapshot inspected; its code was NOT copied. Only the original panel-integrated HTML
template is included.

## Preserved upstream state and intentionally breaking RPC change

- The full GPL-3.0 Python node bridge source (upstream `pasarguard-node-bridge` v0.9.4) is integrated in `waylane_node_bridge/`, with original LICENSE and source attribution. No external PyPI bridge package is installed. Generated Protobuf descriptors and gRPC method paths now use the WayLane namespace; older pre-WayLane node/bridge binaries cannot connect to this renamed RPC service.
- `waylane.*` NATS subjects and `waylane_*` JetStream KV buckets are defaults for a **new** deployment. Prior clusters with persisted `pasarguard.*` subject/bucket names need a separately planned state migration; no dual-read is automatically enabled.
- Existing provider API response shapes and subscription tokens remain unchanged. The bundled legacy panel-node RPC namespace is intentionally incompatible with pre-WayLane clients; both sides must be upgraded together.
- The container exposes `waylane-cli` and `waylane-tui` directly. Historic command aliases are not required by the fresh deployment.
- Runtime storage paths and log names are not silently migrated.

**Licensing:** existing GitHub root `LICENSE` (MIT) is retained as authored by Koba Production, but derived PasarGuard code is governed by `LICENSE.UPSTREAM` (AGPL-3.0 for panel/frontend or GPL-3.0 for node). See `LICENSING.md`.

## Preserved Alembic lineage

The 160 original PasarGuard Panel migration files from the imported snapshot
(`a628af3`, sourced from the upstream commit pinned above) were restored
without modification. Their exact file hashes are tracked in
`app/db/migrations/legacy_history_sha256.json`. The historical tip
`c8d2e4f6a1b3` precedes the new WayLane compatibility bridge
`21bec95874f4`; none of the original migration IDs have been replaced.

The former collapsed `waylane_0001` create-all baseline was discarded as an
active migration: executing it against an existing PasarGuard database would
attempt to recreate existing tables. Use the full preserved history for fresh
installs and the in-place forward upgrade path for existing panels.
