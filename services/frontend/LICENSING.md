# License scope and upstream attribution

This GitHub repository was initialized by Koba Production with the MIT license
in `LICENSE`. That MIT license is retained **unchanged** as part of the remote
repository's authoritative initial content. It does **not** relicense source
derived from PasarGuard Panel dashboard.

The imported PasarGuard Panel dashboard code and derivative modifications remain subject to
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

## Bundled configuration libraries

The three `vendor/*` source packages are adapted from upstream
AGPL-3.0-licensed configuration kits. Each retains a full `LICENSE`,
`COMMERCIAL_LICENSE.md`, and `UPSTREAM.md` documenting the pinned source
revision. Their use does not turn the code into MIT-licensed WayLane code.
The frontend now builds these packages itself instead of fetching the
upstream published npm artifacts.
