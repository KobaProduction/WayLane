# WayLane Node — Git-backed deployment sources

**Deployment definitions live here; runtime source stays in
`services/node/bootstrap/` and `services/node/agent/`.**

A Node is one installation comprising exactly two outer Compose services:
a privileged **one-shot bootstrap** followed by one non-root **agent**
supervising its own private rootless Docker and VPN core containers. A
Classic or Discovery Node is a **registration mode**, not another runtime.

| Mode | Entrypoint | Enrollment |
| --- | --- | --- |
| **Discovery** (preferred for the first isolated DEV test) | [discovery/](discovery/) | One protected `DISCOVERY_TOKEN`; the Node enrolls itself |
| Classic | [classic/](classic/) | One protected `NODE_TOKEN`; the panel pre-creates an identity |

Each directory independently owns a **standalone**
`docker-compose.yaml` and a Coolify `docker-compose.coolify.yaml`,
plus its operational README. These are separate alternatives; **use one
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
  `services/node/bootstrap/Dockerfile` and
  `services/node/agent/Dockerfile`. Coolify builds directly from Git.
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

## Watch Paths — manually save in Coolify

Discovery resource:

```text
deploy/node/discovery/**
services/node/bootstrap/**
services/node/agent/**
```

Classic resource:

```text
deploy/node/classic/**
services/node/bootstrap/**
services/node/agent/**
```

These are **repository-relative**, newline-separated patterns. A webhook
alone cannot configure Watch Paths; save them under the individual Coolify
resource. Documentation changes inside that mode's directory deliberately
affect that mode only. Source shared by both modes triggers both only after
those resources are actually installed. Keep automatic deployments disabled
for an unaccepted traffic-serving Node; the operator owns rollout decisions.

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
back up the identity volumes before a planned migration. Keep existing
PasarGuard containers, credentials and client routing unchanged.
See [Node architecture](../../services/node/agent/README.md) and
[wire protocol](../../shared/contracts/NODE_CONTROL.md).
