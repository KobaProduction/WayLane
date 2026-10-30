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

- GitHub repository: `KobaProduction/WayLane`; release/DEV branch chosen
  by the operator, normally `main` after approved merge.
- Build context: **monorepo root**; Dockerfiles are
  `services/agent/bootstrap.Dockerfile` and
  `services/agent/agent.Dockerfile`. Coolify builds directly from Git.
  No Docker registry publication, prerelease tag or external standalone
  Node repository is needed for the first pilot.
- Coolify Base Directory: `/deploy/node/discovery` **or**
  `/deploy/node/classic`; Compose Location for either:
  `/docker-compose.coolify.yaml`. The service directory
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

## Watch Paths — save in Coolify

**Discovery:**

```text
deploy/node/discovery/*.yaml
services/agent/**
!services/agent/**/*.md
```

**Classic:**

```text
deploy/node/classic/*.yaml
services/agent/**
!services/agent/**/*.md
```

Paths are repository-relative. The first pattern watches only that mode's
two Compose files; the second watches all Node source (Agent Dockerfiles,
bootstrap script and stable Agent runtime). The final
negation deliberately excludes Node Markdown documentation. The common
`deploy/node/README.md` is outside both resource Watch Paths, so README
changes alone do **not** trigger Coolify's Git webhook deployments.
Coolify evaluates Watch Paths in order; keep the exclusion **after**
the inclusive source pattern. A manual deploy is not blocked by Watch Paths.
Save and verify these patterns in the actual Coolify resource.

## Discovery — preferred first isolated DEV Node

The Node self-registers using an invitation issued in the panel under
**Nodes → Discovery**. It may remain pending until the operator approves
it. Do not precreate a Classic identity.

| Coolify field | Value |
| --- | --- |
| Git repository | `KobaProduction/WayLane` |
| Branch | `main`, after approved merge |
| Base Directory | `/deploy/node/discovery` |
| Compose Location | `/docker-compose.coolify.yaml` |
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
| Branch | `main`, after approved merge |
| Base Directory | `/deploy/node/classic` |
| Compose Location | `/docker-compose.coolify.yaml` |
| Build Pack | Docker Compose |

Set `CONTROL_PANEL_URL` to the trusted public HTTPS Panel origin and
`NODE_TOKEN` to the protected one-time credential.
`DISCOVERY_TOKEN` is always empty, `DEV_ALLOW_HTTP` is forced false.
No extra DNS listener, gRPC target or published Node management port is
needed. Both profiles build the same Node runtime.

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

## Runtime secrets and acceptance

The only operator values are `CONTROL_PANEL_URL` (required HTTPS origin)
and **exactly one** mode-specific secret:
`DISCOVERY_TOKEN` (Discovery) or `NODE_TOKEN` (Classic).
Never commit or expose the token to the image build. Coolify should store
it as a protected, runtime-only secret; no placeholders with live values.
`DEV_ALLOW_HTTP` is **forced false by the Coolify adapter**.

First validate Coolify's **parsed Deployable Compose**, rather than just
YAML: two services, successful one-shot bootstrap (0), non-root agent
UID/GID 1000, private volumes, host hostname mount, no published management
ports and correct protected variables. Then on an isolated test server
check the rootless daemon, HTTPS enrollment, authorized WSS Protobuf
hello/ack and heartbeats, retained identity across restart and a single
non-public Xray core action. Rootless networking and public TCP/UDP VPN
client traffic are **not yet production accepted**.

Roll back by selecting the previous approved Git revision **without
destroying volumes**, checking bootstrap/agent and restored WSS control.
A source rollback cannot reverse changed persistent registration data;
back up the identity volumes before a planned migration. The pilot must not modify unrelated services, credentials, or client routing.
See [Node architecture](../../services/node/README.md) and
[wire protocol](../../shared/contracts/NODE_CONTROL.md).
