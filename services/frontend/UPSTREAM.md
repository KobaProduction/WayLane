Dashboard extracted from https://github.com/PasarGuard/panel
commit `5eb8628cfe6db233ed2a6bbaf53f0ed497e797f9` (2026-10-24).
Originally path: `dashboard/`; Python integration module `dashboard/__init__.py`
remains in `services/backend/`.
Preserve PasarGuard AGPL-3.0 and copyright attribution. The imported dashboard source itself was not rewritten as Vue.
No external subscription-template source is imported.

## Legacy compatibility intentionally retained

- `@pasarguard/*` npm modules are external published validation/configuration dependencies. Do not rename imports until corresponding WayLane packages have been published.
- Existing `pasarguard-*` browser preference storage keys are retained so current users' settings survive rebranding.
- Release checks now target WayLane's own GitHub repositories; without published releases, no updater should claim newer versions.
- Upstream fundraising, donations and advertising fetches have been removed from the WayLane UI.
- Original artwork was not redistributed as a WayLane logo. A dedicated identity/favicon is a separate design task.

**Licensing:** existing GitHub root `LICENSE` (MIT) is retained as authored by Koba Production, but derived PasarGuard code is governed by `LICENSE.UPSTREAM` (AGPL-3.0 for panel/frontend or GPL-3.0 for node). See `LICENSING.md`.

## Third-party configuration kit namespace

The WayLane frontend imports `@waylane/core-kit`, `@waylane/xray-config-kit` and `@waylane/wireguard-config-kit`. These currently resolve through pinned npm aliases to upstream `@pasarguard/*` published packages. Bun lockfile retains their actual registry identities and integrity hashes; this is not a new library fork or a protocol change.
