# License scope and upstream attribution

This GitHub repository was initialized by Koba Production with the MIT license
in `LICENSE`. That MIT license is retained **unchanged** as part of the remote
repository's authoritative initial content. It does **not** relicense source
derived from PasarGuard Panel.

The imported PasarGuard Panel code and derivative modifications remain subject to
**AGPL-3.0** and the original authors' copyright notices; the upstream
license text is preserved verbatim in `LICENSE.UPSTREAM`. Read `UPSTREAM.md`
for the source repository and exact imported revision.

Independently written code may have additional licensing terms, but must not
override the corresponding upstream copyleft requirements. Do not describe
the entire derived WayLane implementation as solely MIT-licensed without a
separate rights review.

## Intended MIT-only transition

WayLane aims to become an independently implemented MIT-licensed project.
Existing upstream-derived code does **not** become MIT by renaming names,
reformatting, relocating files, or deleting a license notice. Remove the
upstream code and the corresponding copyleft obligations only once a
verified clean replacement is available; maintain attribution until then.

## Retired Python Node bridge — historical GPL-3.0 source

The prior `waylane_node_bridge/` package was adapted from the GPL-3.0
`pasarguard-node-bridge` implementation. The legacy runtime, schemas and
NodeService transport are retired under Issue #21, but the exact source,
copyright, GPL-3.0 license and upstream attribution remain in repository Git
history. Deleting current source does not erase historical GPL obligations,
and the root MIT license does not relicense any remaining derived material.
The active Node transport is the independent Agent/Node binary WSS implementation.
