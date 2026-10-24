Upstream https://github.com/PasarGuard/node at
`47857d43fe777f30f9ecc5f2ffe4ca0b037507cb`, retrieved 2026-10-24.
GPL-3.0 upstream license and attribution preserved in `LICENSE.UPSTREAM`.

## Legacy compatibility intentionally retained

- `PG_NODE_*` environment variables and `/var/lib/pg-node` data directories stay unchanged until an explicit migration with aliases and data movement.
- `common/service.proto` retains its old protobuf `go_package` and generated descriptor because a separately published Python bridge consumes the same transport protocol; binary wire compatibility is required.
- Existing Xray-managed guard tags and placeholder identities are retained to avoid runtime changes.
- `XRAY_INSTALLER_URL` still points to a pinned upstream installer; this is an explicit external build dependency, not an Intens-controlled artifact.

**Licensing:** existing GitHub root `LICENSE` (MIT) is retained as authored by Koba Production, but derived PasarGuard code is governed by `LICENSE.UPSTREAM` (AGPL-3.0 for panel/frontend or GPL-3.0 for node). See `LICENSING.md`.
