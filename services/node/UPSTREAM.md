Upstream https://github.com/PasarGuard/node at
`47857d43fe777f30f9ecc5f2ffe4ca0b037507cb`, retrieved 2026-10-24.
GPL-3.0 upstream license and attribution preserved in `LICENSE.UPSTREAM`.

## Legacy compatibility intentionally retained

- `PG_NODE_*` environment variables and `/var/lib/pg-node` data directories stay unchanged until an explicit migration with aliases and data movement.
- `common/service.proto` retains its old protobuf `go_package` and generated descriptor because a separately published Python bridge consumes the same transport protocol; binary wire compatibility is required.
- Existing Xray-managed guard tags and placeholder identities are retained to avoid runtime changes.
- `XRAY_INSTALLER_URL` still points to a pinned upstream installer; this is an explicit external build dependency, not an Intens-controlled artifact.

**Licensing:** existing GitHub root `LICENSE` (MIT) is retained as authored by Koba Production, but derived PasarGuard code is governed by `LICENSE.UPSTREAM` (AGPL-3.0 for panel/frontend or GPL-3.0 for node). See `LICENSING.md`.


## AmneziaWG 3.1 external runtime

The optional Linux AmneziaWG userspace device is built from
`github.com/amnezia-vpn/amneziawg-go/v3@v3.1.20260828`
(upstream commit `b5928efb6ca19f0153958460c3d141f04abc5c2e`).
Its MIT license is preserved in `LICENSE.AMNEZIAWG` and shipped
inside the node image. No upstream AWG source is vendored into this repository.

`engine=amneziawg` inside an existing `wg` core uses that binary and
its `/var/run/amneziawg/<interface>.sock` UAPI. An omitted engine keeps
the pre-existing Linux kernel WireGuard implementation and existing protobuf
wire format. AWG 3.1 requires matching client config for obfuscated
handshakes; compatibility with plain WireGuard only when obfuscation disabled.
The panel rejects node versions older than v0.6.0 for AWG.
