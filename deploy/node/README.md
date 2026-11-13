# WayLane Node — Git-backed deployment sources

**Each installation has exactly two outer Compose services:** a privileged
one-shot bootstrap plus the stable non-root Agent. Agent starts embedded
rootless Docker and supervises a separately updatable Node image. Node owns
Panel WSS and manages its inner VPN Core containers. There is no extra outer
Node service or system Docker socket. Classic and Discovery are independent
registration options under this same runtime.

| Mode | Entrypoint | Enrollment |
| --- | --- | --- |
| Discovery | [discovery/](discovery/) | Invitation + approval |
| Classic | [classic/](classic/) | Panel-issued individual Node token |

Each mode has generic and Coolify Compose manifests.

## Source, build and deployment boundary

- GitHub repository: `KobaProduction/WayLane`; use an explicitly accepted
  and pinned source revision for a disposable development pilot. Source
  changes are validated locally and pushed directly to `main` according
  to the root `AGENTS.md`; no PR/merge is required by default. Node
  deployments remain manual and independently approved.
- Build context: **monorepo root**; Dockerfiles are
  `services/agent/bootstrap.Dockerfile` and
  `services/agent/agent.Dockerfile`. Coolify builds directly from Git.
  No Docker registry publication, prerelease tag or external standalone
  Node repository is needed for the first pilot.
- Coolify Base Directory: `/deploy/node/discovery` **or**
  `/deploy/node/classic`; Compose Location for either:
  `/docker-compose.yaml` **when using Raw (deploy file as-is)** under
  Advanced → Docker compose. The managed parser adds an incompatible
  `networks` entry to the bootstrap `network_mode: none` and Agent
  `network_mode: host`; using `docker-compose.coolify.yaml` in Raw mode
  causes a circular `extends` because Coolify renames it to
  `docker-compose.yaml` at runtime. Use the self-contained generic file.
  The tracked `/docker-compose.coolify.yaml` adapter still exists as a
  managed-parser source, but it is **not** the accepted tambov entrypoint.
  The service directory
  `/services/node` is **not** the Coolify Base Directory.
- Compose project, volumes and credentials are unique to the selected
  installation. For multiple Nodes on one host assign distinct Compose
  project names in the orchestrator; never share the private volumes.
- One public HTTPS `CONTROL_PANEL_URL` for enrollment and outbound
  Protobuf-binary WSS `/api/node-control/control/ws`. The Node has **no**
  management domain, published host port or rootful host Docker socket.
- Persistent named volumes: `agent-state`, `rootless-data`,
  `rootless-run`; **never delete them during redeploy**. Host
  `/etc/hostname` is read-only and supplies the Node name.

## Deployment updates

The stable Agent and Node must **not** restart on arbitrary source pushes.
Keep both Classic and Discovery Node resources on **manual deployment only**
until isolated runtime acceptance, and always inspect the intended commit
before deploying. `Watch Paths` are not a required part of this manual
deployment contract; parser metadata in Compose does not enable webhooks.

If the operator later explicitly enables GitHub App automatic deployments,
scope a given Node installer to its own `deploy/node/<mode>/*.yaml` and
`services/agent/**`, excluding documentation-only changes as appropriate.
Do not automatically replace the Agent for an independently updatable Node
release; its pinned digest is managed through the private Unix API.

## Discovery — preferred first isolated DEV Node

The Node self-registers using an invitation issued in the panel under
**Nodes → Discovery**. It may remain pending until the operator approves
it. Do not precreate a Classic identity.

| Coolify field | Value |
| --- | --- |
| Git repository | `KobaProduction/WayLane` |
| Branch | `main` after locally validated direct push; pin the tested commit for a pilot |
| Base Directory | `/deploy/node/discovery` |
| Compose Location | `/docker-compose.yaml` (Raw mode) |
| Build Pack | Docker Compose |

Set `CONTROL_PANEL_URL` to the trusted public HTTPS Panel origin and
`DISCOVERY_TOKEN` to the protected `wl_disc_...` invitation.
`NODE_TOKEN` is always empty, `DEV_ALLOW_HTTP` is forced false. The
node learns its own name from Linux `/etc/hostname`, and communicates
over one outbound binary-Protobuf WSS connection. The panel must approve
the Node before it receives core-management commands.

## Classic — pre-registered Node

The panel administrator creates the Classic Node and receives its one-time
`wl_node_...` credential, which is stored as protected `NODE_TOKEN`.
The Node receives its host name from Linux `/etc/hostname` on first
authenticated telemetry.

| Coolify field | Value |
| --- | --- |
| Git repository | `KobaProduction/WayLane` |
| Branch | `main` after locally validated direct push; pin the tested commit for a pilot |
| Base Directory | `/deploy/node/classic` |
| Compose Location | `/docker-compose.yaml` (Raw mode) |
| Build Pack | Docker Compose |

Set `CONTROL_PANEL_URL` to the trusted public HTTPS Panel origin and
`NODE_TOKEN` to the protected one-time credential.
`DISCOVERY_TOKEN` is always empty, `DEV_ALLOW_HTTP` is forced false.
No extra DNS listener, gRPC target or published Node management port is
needed. Both profiles build the same Node runtime.

### Server user namespace policy

On `tambov`, Linux has `kernel.apparmor_restrict_unprivileged_userns=1`
and `kernel.apparmor_restrict_unprivileged_unconfined=1`. With
`apparmor=unconfined`, RootlessKit cannot create its child namespace;
under `docker-default`, namespace creation succeeds but the kernel
transitions to the restricted `unprivileged_userns` profile and denies
`CAP_SYS_ADMIN` needed for a private namespace mount. The host already
provides an installed `rootlesskit` AppArmor profile with an explicit
`userns,` rule. An ephemeral non-root container using that profile
passed `unshare -Ur -m` and `mount --make-rprivate /` inside the isolated
mount namespace. The Agent therefore requires `apparmor=rootlesskit`
and `seccomp=unconfined`. Additionally, Docker masks `/proc` paths in
outer containers by default; nested rootless runc then fails to mount
its own procfs (even for a minimal `docker run`). Set
`systempaths=unconfined` on **only the non-root Agent**. This broadens
access to previously masked kernel paths within that container, so it
must remain UID/GID 1000, read-only rootfs, with dropped capabilities
except SETUID/SETGID and a private Docker socket. On tambov, the exact
configuration (with the existing `/dev/net/tun` device) passed an
isolated rootless Docker nested-container test, and rebuilt the Node
seed including the `RUN` instruction. Do not disable global AppArmor
userns controls or mount a rootful host Docker socket.

Verify the installed `rootlesskit` profile and the full nested runtime on
each host. These tests do not by themselves confirm enrollment, WSS,
Xray execution or externally reachable TCP/UDP VPN traffic.

## Host network and dynamic ports

The outer Agent uses `network_mode: host`. Replaceable Node and VPN
Cores use host networking on private rootless Docker 29.5 or later.
There is no static Compose `ports:` map: a Core can change its UDP/TCP
listeners at runtime without reinstalling Agent. The physical Linux host
firewall/cloud security rules, Docker Desktop host networking, IPv6,
privileged ports (<1024) and TUN/UDP must be accepted separately.

## Experimental AWG 3.1 — isolated Docker Desktop only

Both generic Compose profiles default `AWG_EXPERIMENTAL_DEV` to `false`.
For a disposable local pilot only, an operator may explicitly opt in with
`AWG_EXPERIMENTAL_DEV=true`, `DEV_ALLOW_HTTP=true`, `CONTROL_PANEL_URL` pointing
to Docker Desktop or loopback HTTP, a Linux amd64 Node and a TUN character
device. The child is pinned to the official `amneziavpn/amneziawg-go` 3.1
amd64 image digest and managed through private Rootless Docker. The panel
requires a separately approved Node with advertised AWG support before it
allows AWG desired-state generation. Persistent per-Core state is separate
from Xray. **Coolify hard-disables both DEV flags** and must never be used
for this experiment. Do not consider a Docker image build, AWG UAPI test or
successful control handshake proof of UDP/TUN reachability. Require an isolated
host test for container device/NET_ADMIN permissions, peer handshake, client
traffic, routing, rollback and reconnect before any real VPN exposure.
The outer bootstrap/agent Compose publishes no UDP port, and the AWG child
currently uses `--network host` only within the **nested rootless** runtime.
A working UAPI socket and TUN character device in the outer Node do not
prove externally reachable VPN ingress. Inspect the entire host → outer
Compose → rootless daemon → child UDP forwarding chain and explicitly
approve its networking model before adding public port mappings.

## Isolated development pilot — 2026-11-12

On KOBA-LAPTOP (Docker Desktop / Ubuntu WSL2), an isolated Compose project
built from `main` at `0b3f7a1` was exercised with a synthetic local
Discovery target, **not** the public WayLane panel. Observed acceptance:

- One-shot bootstrap exited 0; stable non-root Agent started embedded rootless
  Docker 29.5.3, with private persistent volumes and no public management port
- Agent rebuilt its local Node seed from the bundled binary after source
  update; the Node container's immutable image ID matched the seed image ID
- Inner Node ran in the private user namespace, mapped its UID 0 to outer
  non-root UID 1000, and created `identity.json` with mode 0600
- Node reached `POST /api/node-control/enroll` at the intentionally absent
  local test endpoint, which returned connection refused; no real credentials
  or live WayLane enrollment were attempted

The synthetic pilot has since been **fully removed from KOBA-LAPTOP** at the
operator's request: all WayLane pilot containers, images, named volumes and
its temporary repository clone were removed. No laptop Node is installed.
That *former laptop pilot* did not authenticate to the panel or test VPN
traffic. The live tambov server acceptance is documented separately below.

## Current tambov development resource — 2026-11-12

Coolify `WayLane / development` has the independent **WayLane / Node**
resource (`o3tsn2bdcpxyhtqshpzfosfw`), sourced from
`KobaProduction/WayLane` `main` on `tambov`, destination
`WayLane Development`, base `/deploy/node/discovery`,
`/docker-compose.yaml` with **Raw (deploy file as-is)** selected, on
accepted commit `f56475f`. Deployment is **manual only**, without a
separate public Node management domain or published host ports.

Observed on the real server (not just source tests):

- One-shot bootstrap exited **0**; the persistent Agent is **healthy**
  and remains UID/GID 1000 with private rootless Docker
- The nested `waylane-node-runtime` is **running**, with zero recorded
  container restarts at the initial inspection, seeded from the approved
  Agent image; the three dedicated named volumes are preserved
- A real panel-issued Discovery invitation (ID 1, 24-hour lifetime,
  auto-approve) enrolled one Node. PostgreSQL registration status is
  **approved**, and its `machine_metrics` and `last_seen_at` are updated
  over the authenticated binary-Protobuf WSS control channel
- The invitation was **revoked** after enrollment, preventing further
  registrations with that token. `DISCOVERY_TOKEN` remains stored in
  Coolify as a protected **runtime-only** setting, never a build argument;
  the already-enrolled Node reconnects with its own persistent identity
- A controlled outer Agent restart completed successfully: Agent returned
  **healthy**, the same nested Node container resumed, and the next
  PostgreSQL inspection showed `approved`, non-null `machine_metrics`,
  and a heartbeat only **12 seconds** old after restart

The Node's machine metrics are persisted by the WayLane Backend via WSS,
**not** sent directly to SigNoz as OTLP. Backend's own OTLP exporter is a
separate observability channel. Node enrollment, remote control transport
and telemetry passed acceptance on 2026-11-12. A first non-public Xray
Core action and TCP proxy egress passed on 2026-11-13 (see below).
**Public VPN access and end-user client traffic are not accepted.**
Experimental AmneziaWG remains disabled.

## Minimal Xray TCP example — 2026-11-13

An **explicitly manually applied** Xray desired-state example is tracked in
[examples/xray-loopback-socks5.json](examples/xray-loopback-socks5.json).
It has exactly one SOCKS5 inbound bound to `127.0.0.1:18443`, UDP disabled,
and a direct outbound. **It has no authentication, so it must never be
bound to a public or LAN address.** Nothing in the example deploys
automatically or changes the Node's managed authentication.

The tambov Node applied this JSON as Xray `26.10.21` desired generation
`1`. PostgreSQL reported `applied_generation=1`,
`runtime_status=running` and no error. The Agent-owned private rootless
Docker showed `waylane-xray` running. A disposable curl container on the
**same private rootless network** made an HTTPS request through SOCKS5 and
received HTTP `200` from `https://example.com/`. Host `ss` showed no
TCP listener on port 18443 and the Xray container has no host port
bindings. Thus, the demo exercises the WSS → Node → Core → outbound TCP
path without opening a remotely accessible proxy or VPN.

This is not production client acceptance: real VLESS/TLS/Reality inbound,
subscription provisioning, public TCP/UDP routing, firewall policies,
traffic accounting and a client tunnel have **not** been tested. Remove or
replace this loopback demonstration through the authenticated Xray desired
state API before a public rollout.

## Runtime secrets and acceptance

The only operator values are `CONTROL_PANEL_URL` (required HTTPS origin)
and **exactly one** mode-specific secret:
`DISCOVERY_TOKEN` (Discovery) or `NODE_TOKEN` (Classic).
Never commit or expose the token to the image build. Coolify should store
it as a protected, runtime-only secret; no placeholders with live values.
`DEV_ALLOW_HTTP` is **forced false by the Coolify adapter**.

For this Node profile, verify Coolify's **Raw effective Compose** (not the
managed parser): two services, successful one-shot bootstrap (0), non-root
Agent UID/GID 1000, private volumes, read-only host hostname mount, no
published management ports and protected runtime-only credentials. Server
`tambov` has passed nested rootless Docker startup, HTTPS enrollment,
authorized WSS/Protobuf heartbeats and retained identity after restart.
A non-public loopback-only Xray Core action and outbound SOCKS5
TCP request have been accepted on the live Node (see example above).
Public TCP/UDP reachability and external VPN-client traffic remain
**unaccepted**.

Roll back by selecting the previous approved Git revision **without
destroying volumes**, checking bootstrap/agent and restored WSS control.
A source rollback cannot reverse changed persistent registration data;
back up the identity volumes before a planned migration. The pilot must not modify unrelated services, credentials, or client routing.
See [Node architecture](../../services/node/README.md) and
[wire protocol](../../shared/contracts/NODE_CONTROL.md).


### Optional read-only Xray StatsService reporting

`WAYLANE_XRAY_STATS_READONLY` defaults to `false` in both Classic and
Discovery Compose profiles and their Coolify adapters. To permit the replaceable
Node to query its private Xray StatsService and send bounded user counter
observations on the existing binary WSS, set it explicitly to `true` **only
after** validating the Backend `c41a5e8dbe23` migration, Node image, and
correct `stats`/`policy` settings. The stable Agent carries the opt-in into the
private Node env file; the Node must be independently replaced to pick up a
changed env value. This is not a hot configuration change and never grants
commercial billing authority. Do not change existing live Node deployments
solely to turn this on without explicit operator acceptance.
