Source: https://github.com/PasarGuard/panel at commit
`5eb8628cfe6db233ed2a6bbaf53f0ed497e797f9`, retrieved 2026-10-24.
AGPL-3.0 license and upstream copyright notices remain intact in `LICENSE.UPSTREAM`.
The original dashboard React/TypeScript source was extracted into
`intens-cloud/waylane-frontend`; the Python dashboard serving shim remains here.
No source ownership is transferred by this split. Preserve original attribution.
A separate repository PasarGuard/subscription-template has no root LICENSE in the
snapshot inspected; its code was NOT copied. Only the original panel-integrated HTML
template is included.

## Legacy compatibility intentionally retained

- `PasarGuardNodeBridge` and `pasarguard-node-bridge` are **third-party Python dependency names**, not the WayLane product name. Code imports them as published.
- `pasarguard.*` NATS subjects and `pasarguard_*` JetStream KV buckets are existing persistent protocol/state identifiers and remain unchanged until a dual-read, dual-write migration.
- Existing provider API response shapes, subscription tokens and panel-node transport remain compatible.
- Old `pasarguard-cli` and `pasarguard-tui` aliases remain temporarily available, while the new commands are `waylane-cli` and `waylane-tui`.
- Runtime storage paths and log names are not silently migrated.

**Licensing:** existing GitHub root `LICENSE` (MIT) is retained as authored by Koba Production, but derived PasarGuard code is governed by `LICENSE.UPSTREAM` (AGPL-3.0 for panel/frontend or GPL-3.0 for node). See `LICENSING.md`.
