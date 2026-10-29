# WayLane Node — runtime, enrollment and VPN core ownership

A **Node** is the deployment installed on a physical or virtual server. Its
outer Docker Compose contains exactly two services:

- `bootstrap`: privileged one-shot initializer of private persistent volumes
- `agent`: non-root internal controller (UID/GID 1000), supervising the
  RootlessKit + dockerd daemon **inside the agent container**

VPN **cores** (Xray first; other drivers pending) are independent child
containers controlled by this internal agent. A Node can later own several
cores. One server usually hosts one Node; two Nodes may coexist only with
isolated Compose project names, volumes, identity, networking and ingress.
No systemd, host Docker socket, extra rootless Compose sidecar or direct
Panel-to-Docker control is permitted.

## Two exclusive node registration profiles

The canonical **deployment definitions** are at
[deploy/node/](../../../deploy/node/README.md); this source directory
contains only the internal Node runtime implementation. Each registration
mode owns one complete generic Compose, one Coolify adapter and a README:

| Registration | Deployment directory | Credential |
| --- | --- | --- |
| [Classic](../../../deploy/node/classic/README.md) | `deploy/node/classic/` | `NODE_TOKEN` |
| [Discovery](../../../deploy/node/discovery/README.md) | `deploy/node/discovery/` | `DISCOVERY_TOKEN` |

Both entrypoints have exactly the same runtime security, two services and
`agent-state`, `rootless-data`, `rootless-run` private volumes.
**Never** mount one Node's state/identity volume into a second Node.
`bootstrap` completes 0 and exits; the long-lived internal `agent`
runs with UID 1000 and supervises its private socket
`/run/user/1000/docker.sock`. Coolify adapters mark the completed
bootstrap `exclude_from_hc: true`.

**Classic:** An authorized administrator creates a new Node in the panel
(`POST /api/node-registry/classic`, no name or body required), receives the
one-time `wl_node_...` `NODE_TOKEN` and places it in the protected deployment
variable. The hostname is not configured in the panel. The Node
does not use a shared Discovery invitation; it starts with its existing UUID
and panel-side approved registration.

**Discovery:** An authorized administrator creates a `wl_disc_...`
invitation (`POST /api/node-registry/discovery-tokens`). The Node starts
with `DISCOVERY_TOKEN`, creates a private UUID and node secret, enrolls
exactly once via `POST /api/node-control/enroll` and is either automatically
approved or awaits confirmation. Both flows use the same internal
controller/cores once approved. A registered identity is persisted in
`agent-state`; preserve this volume on restart. The Node always reads
the **server's** `/etc/hostname` from a read-only mount, never Docker's
container hostname. If the mount or hostname is invalid, registration fails
rather than inventing a name. A Classic Node receives that hostname on its
first authenticated telemetry; the registry shows a temporary UUID-derived
label before the Node connects. Tokens are returned once,
never committed and never written to logs.

No `wl_agent_` token, prefixed `WAYLANE_AGENT_*` deployment inputs or
old `/api/agent` / `/api/node-discovery` endpoints are supported.
There are **no compatibility aliases or runtime downgrade modes**.
Changing an already registered Node's UUID requires an explicit, controlled
identity reset; do not silently rebind existing data.

## Deployment variables

Only use:

| Variable | Required? | Meaning |
| --- | --- | --- |
| `CONTROL_PANEL_URL` | Yes | One public HTTPS **origin** for registration and binary WSS; no URL path |
| `NODE_TOKEN` | Classic profile only | One-time private Node token |
| `DISCOVERY_TOKEN` | Discovery profile only | One-time private invitation |
The private internal runtime uses `NODE_STATE_DIR` and
`ROOTLESS_SOCKET`. Only `DEV_ALLOW_HTTP=true` may allow an explicitly
local Docker Desktop HTTP/WS trial; it is fixed to false in both remote
Coolify adapters. Remote connections require CA-verified HTTPS/WSS.

The **single** Node control transport is a persistent WSS connection with
strictly binary Protobuf `Envelope` frames. The Node derives
`wss://<panel>/api/node-control/control/ws` from
`CONTROL_PANEL_URL` and initiates one authenticated session. The Panel
pushes desired commands and the Node sends result, telemetry and
acknowledgements across the same full-duplex connection. There is no
gRPC target, transport selector, extra listener or fallback protocol.
See `shared/contracts/NODE_CONTROL.md` for the authoritative wire contract.

The Panel frontend's same-origin Nginx configuration already declares the
binary WebSocket Upgrade route. Acceptance still requires a **real**
trusted TLS WSS handshake through the deployed proxy; source checks alone
do not prove the route works end-to-end.

## Git-backed deployment

Use the [Discovery deployment](../../../deploy/node/discovery/README.md)
for the first isolated DEV pilot, or
[Classic](../../../deploy/node/classic/README.md) for a Node explicitly
precreated in the panel. Their Coolify Base Directories are
`/deploy/node/discovery` and `/deploy/node/classic`, respectively;
both use `/docker-compose.coolify.yaml`. Save the Watch Paths stated
in each deployment README, not `services/node/**` indiscriminately.
The same trusted GitHub monorepo remains the build source.

No public management domain, system Docker socket, alternative transport
or node-name variable is needed. Protected credentials belong only in
runtime configuration. Bootstrap and agent retain separate permissions
inside the same resource.

## Security and acceptance

The nested daemon uses limited `SETUID`/`SETGID`, `/dev/net/tun`,
`seccomp=unconfined` and `apparmor=unconfined` in the current test setup.
These exceptions reduce isolation. Docker Desktop's nested bridge IPv6
sysctl restriction is still unresolved; inner host-namespace networking
has only had isolated DEV TCP SOCKS5 acceptance, **not production VPN ingress**.

Source CI/tests prove schemas, Go control, Docker images and Compose
structure. A real isolated host pilot must separately prove bootstrap exit,
non-root agent, private daemon health, panel TLS/auth enrollment, binary WSS hello/ack, heartbeats, revocation, identity persistence, single Xray
desired/application with real TCP and UDP checks, and safe rollback.
Full PasarGuard Xray user/stats/routing parity, sing-box, WireGuard,
AmneziaWG and multicore routing remain separate tasks. Do not stop or
modify existing PasarGuard nodes, certificates, subscriptions and client
routing until explicit migration acceptance.

Related tasks: WayLane Issues #11, #13, #14, #15 and #17.
