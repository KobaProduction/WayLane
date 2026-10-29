# Discovery Node — preferred DEV pilot

This is an independent Git-backed WayLane Node installation. The Node
self-enrolls using a one-time Discovery invitation created in WayLane Panel
(`Nodes → Discovery`); it may need explicit approval before accepting
core commands. **Do not precreate a Classic identity for this mode.**

## Coolify resource (operator action)

| Field | Value |
| --- | --- |
| Repository | `KobaProduction/WayLane` |
| Branch | `main` (after reviewed merge) |
| Base Directory | `/deploy/node/discovery` |
| Compose Location | `/docker-compose.coolify.yaml` |
| Build Pack | Docker Compose |
| Watch Paths | See below |

Watch Paths (persist each line in Coolify):

```text
deploy/node/discovery/**
services/node/bootstrap/**
services/node/agent/**
```

`docker-compose.yaml` is the standalone runnable entrypoint;
`docker-compose.coolify.yaml` extends it only for the required-variable
parser and completed-bootstrap health policy. Both build from the Git
monorepo root; they do not publish container images.

| Variable | Policy |
| --- | --- |
| `CONTROL_PANEL_URL` | Required full trusted HTTPS origin of the **existing** WayLane panel |
| `DISCOVERY_TOKEN` | Required protected runtime-only invitation, `wl_disc_...`, never in Git |
| `NODE_TOKEN` | Explicit empty string — Classic registration is excluded |
| `DEV_ALLOW_HTTP` | Forced `false` for remote Coolify |

The Node reads its Linux host `/etc/hostname` as its name and opens
**one outbound WSS** binary-Protobuf channel to the panel. No manual name,
gRPC endpoint, published management port or host Docker socket is needed.
The initial invitation is not an admin credential and does not authorize
production VPN client routing.

After first deployment verify private volumes, bootstrap exit 0, agent
rootless health, approval, WSS handshake and retained identity after
restart. Never `down -v` the accepted Node. See the
[parent deployment contract](../README.md) for rollback and isolation.
