# WayLane Node — Git-backed deployment sources

**Deployment definitions live here; runtime source remains in
`services/node/`: a small `bootstrap.sh`, `bootstrap.Dockerfile` and
the Go main package built by `node.Dockerfile`.**

A Node is one installation comprising exactly two outer Compose services:
a privileged **one-shot bootstrap** followed by one non-root **Node**
supervising its own private rootless Docker and VPN core containers. A
Classic or Discovery Node is a **registration mode**, not another runtime.

| Mode | Entrypoint | Enrollment |
| --- | --- | --- |
| **Discovery** (preferred for the first isolated DEV test) | [discovery/](discovery/) | One protected `DISCOVERY_TOKEN`; the Node enrolls itself |
| Classic | [classic/](classic/) | One protected `NODE_TOKEN`; the panel pre-creates an identity |

Each directory independently owns a **standalone**
`docker-compose.yaml` and a Coolify `docker-compose.coolify.yaml`,
with the single operational README in this parent directory. These are separate alternatives; **use one
mode per Node**. Each runtime is fully self-contained, rather than
inheriting a second mode or reaching into `services/node` for Compose.
A source contract test compares their common security/runtime topology
so changes cannot silently diverge. Only the adapter uses Coolify's
`exclude_from_hc: true` for a completed bootstrap; no third long-running
service is created.

## Source, build and deployment boundary

- GitHub repository: `KobaProduction/WayLane`; release/DEV branch chosen
  by the operator, normally `main` after approved merge.
- Build context: **monorepo root**; Dockerfiles are
  `services/node/bootstrap.Dockerfile` and
  `services/node/node.Dockerfile`. Coolify builds directly from Git.
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
services/node/**
!services/node/**/*.md
```

**Classic:**

```text
deploy/node/classic/*.yaml
services/node/**
!services/node/**/*.md
```

Paths are repository-relative. The first pattern watches only that mode's
two Compose files; the second watches all Node source (both Dockerfiles,
the bootstrap script, single Go command and module dependencies). The final
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
