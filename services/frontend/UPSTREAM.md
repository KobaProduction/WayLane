Dashboard extracted from https://github.com/PasarGuard/panel
commit `5eb8628cfe6db233ed2a6bbaf53f0ed497e797f9` (2026-10-24).
Originally path: `dashboard/`; Python integration module `dashboard/__init__.py`
remains in `services/backend/`.
Preserve PasarGuard AGPL-3.0 and copyright attribution. The imported dashboard source itself was not rewritten as Vue.
No external subscription-template source is imported.

## Legacy compatibility intentionally retained

- Configuration kits are vendored as local `@waylane/*` workspaces with original AGPL-3.0 licensing and copyright preserved.
- New browser preference keys use `waylane-*`; migration of prior browser-local preferences is separate.
- Release checks target component-prefixed tags in the single WayLane monorepo; without published releases, no updater should claim newer versions.
- Upstream fundraising, donations and advertising fetches have been removed from the WayLane UI.
- Original artwork was not redistributed as a WayLane logo. A dedicated identity/favicon is a separate design task.

**Licensing:** existing GitHub root `LICENSE` (MIT) is retained as authored by Koba Production, but derived PasarGuard code is governed by `LICENSE.UPSTREAM` (AGPL-3.0 for panel/frontend or GPL-3.0 for node). See `LICENSING.md`.

## Integrated config kits (source vendoring)

The WayLane frontend builds local workspace packages `@waylane/core-kit`,
`@waylane/xray-config-kit` and `@waylane/wireguard-config-kit` from
`services/frontend/vendor/`. The published `@pasarguard/*` npm dependencies
are no longer downloaded. Per-package `UPSTREAM.md`, `LICENSE`
and `COMMERCIAL_LICENSE.md` preserve upstream provenance and
AGPL-3.0 licensing. The packaged binaries are compiled from checked-in source.
