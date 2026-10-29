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

The source of truth is **one** `services/node/docker-compose.node.yaml`
(base runtime) with two fully separate installation entrypoints:

| Registration | Generic Compose | Coolify Compose | Credential |
| --- | --- | --- | --- |
| Classic | `docker-compose.node.classic.yaml` | `docker-compose.node.classic.coolify.yaml` | `NODE_TOKEN` |
| Discovery | `docker-compose.node.discovery.yaml` | `docker-compose.node.discovery.coolify.yaml` | `DISCOVERY_TOKEN` |

Both entrypoints have exactly the same runtime security, two services and
`agent-state`, `rootless-data`, `rootless-run` private volumes.
**Never** mount one Node's state/identity volume into a second Node.
`bootstrap` completes 0 and exits; the long-lived internal `agent`
runs with UID 1000 and supervises its private socket
`/run/user/1000/docker.sock`. Coolify adapters mark the completed
bootstrap `exclude_from_hc: true`.

**Classic:** An authorized administrator creates a new Node in the panel
(`POST /api/node-registry/classic`), receives the one-time `wl_node_...`
`NODE_TOKEN` and places it in the protected deployment variable. The Node
does not use a shared Discovery invitation; it starts with its existing UUID
and panel-side approved registration.

**Discovery:** An authorized administrator creates a `wl_disc_...`
invitation (`POST /api/node-registry/discovery-tokens`). The Node starts
with `DISCOVERY_TOKEN`, creates a private UUID and node secret, enrolls
exactly once via `POST /api/node-control/enroll` and is either automatically
approved or awaits confirmation. Both flows use the same internal
controller/cores once approved. A registered identity is persisted in
`agent-state`; preserve this volume on restart. If `NODE_NAME` is empty,
the Node reads the **server's** `/etc/hostname` from a read-only mount.
This avoids accidentally displaying Docker's container ID. If the mount or
hostname is invalid, registration fails rather than inventing a name.
A Classic Node created without a display name adopts that hostname on its
first authenticated telemetry; explicitly assigned names are preserved. Tokens are returned once,
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
| `CONTROL_PANEL_URL` | Yes | One public HTTPS **origin** for registration, gRPC and WSS; no URL path |
| `NODE_TOKEN` | Classic profile only | One-time private Node token |
| `DISCOVERY_TOKEN` | Discovery profile only | One-time private invitation |
| `NODE_NAME` | Optional | Override host-machine hostname; by default read host `/etc/hostname` (read-only bind) |
| `CONTROL_TRANSPORT` | No, default `grpc` | `grpc` or `ws` binary Protobuf |


The private internal runtime uses `NODE_STATE_DIR` and
`ROOTLESS_SOCKET`. Local Docker Desktop-only test overrides are
`DEV_ALLOW_HTTP` / `GRPC_DEV_INSECURE`; **both are fixed false
in the remote Coolify adapters**. Never disable certificate validation
for remote panel URLs.

The canonical protocol is `shared/contracts/NODE_CONTROL.md`. gRPC is
full-duplex Protobuf `AgentControl.Connect`, with optional full-duplex binary
WebSocket at `wss://<panel>/api/node-control/control/ws`. They carry the
same `Envelope`. No text frames, SSE, JSON control, HTTP desired polling,
or implicit fallback between transports.

**Important:** The existing bundled frontend proxies WSS but does **not**
by itself expose the separate gRPC listener. The panel backend's
`CONTROL_GRPC_BIND` listener, trusted server certificate and an accessible
HTTP/2 TLS ingress must be configured and verified before a remote Node
can connect with the default `grpc`. Until that is done, an explicitly
selected `CONTROL_TRANSPORT=ws` uses the panel's existing HTTPS origin.
Do not claim a gRPC default means the live ingress is already configured.

## Coolify setup (per Node)

- Git source: `KobaProduction/WayLane`, branch `main`, trusted GitHub App
- Base Directory: `/services/node`
- Compose Location: `/docker-compose.node.classic.coolify.yaml` **or**
  `/docker-compose.node.discovery.coolify.yaml`
- Watch Paths: `services/node/**`, persisted in Coolify (the `x-` key is
  documentation only). Avoid automatic redeploy of unaccepted live traffic.
- Do not assign an HTTP domain or mount `/var/run/docker.sock`. The Node
  initiates outbound connections to the panel.
- Keep tokens protected, runtime-only and inaccessible to image builds.

## Security and acceptance

The nested daemon uses limited `SETUID`/`SETGID`, `/dev/net/tun`,
`seccomp=unconfined` and `apparmor=unconfined` in the current test setup.
These exceptions reduce isolation. Docker Desktop's nested bridge IPv6
sysctl restriction is still unresolved; inner host-namespace networking
has only had isolated DEV TCP SOCKS5 acceptance, **not production VPN ingress**.

Source CI/tests prove schemas, Go control, Docker images and Compose
structure. A real isolated host pilot must separately prove bootstrap exit,
non-root agent, private daemon health, panel TLS/auth enrollment, gRPC or
WSS hello/ack, heartbeats, revocation, identity persistence, single Xray
desired/application with real TCP and UDP checks, and safe rollback.
Full PasarGuard Xray user/stats/routing parity, sing-box, WireGuard,
AmneziaWG and multicore routing remain separate tasks. Do not stop or
modify existing PasarGuard nodes, certificates, subscriptions and client
routing until explicit migration acceptance.

Related tasks: WayLane Issues #11, #13, #14, #15 and #17.
