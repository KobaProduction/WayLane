# Classic Node — panel-precreated registration

This is an independent Git-backed WayLane Node. An authorized panel admin
creates a Classic Node first and copies the issued **one-time individual**
`wl_node_...` credential into protected Coolify `NODE_TOKEN`.

## Coolify resource (operator action)

| Field | Value |
| --- | --- |
| Repository | `KobaProduction/WayLane` |
| Branch | `main` (after reviewed merge) |
| Base Directory | `/deploy/node/classic` |
| Compose Location | `/docker-compose.coolify.yaml` |
| Build Pack | Docker Compose |
| Watch Paths | See below |

Watch Paths (persist each line in Coolify):

```text
deploy/node/classic/**
services/node/bootstrap/**
services/node/agent/**
```

`docker-compose.yaml` runs the standalone bootstrap + agent stack;
`docker-compose.coolify.yaml` extends it for Coolify parser and
one-shot-bootstrap health handling. Both build from this monorepo root.

| Variable | Policy |
| --- | --- |
| `CONTROL_PANEL_URL` | Required full trusted HTTPS origin of the existing WayLane panel |
| `NODE_TOKEN` | Required protected runtime-only `wl_node_...` secret, never in Git |
| `DISCOVERY_TOKEN` | Explicit empty string — Discovery registration is excluded |
| `DEV_ALLOW_HTTP` | Forced `false` for remote Coolify |

The precreated Node has a temporary UUID label until it authenticates and
reports its real Linux host `/etc/hostname`. Control flows over one
outbound **WSS + binary Protobuf** session. Keep its own private volumes
on restarts and preserve the secret boundary. No host Docker socket,
management domain, gRPC target or VPN client traffic cutover is part
of the initial test.

Before activation review the [common Node deployment contract](../README.md)
and do not touch existing PasarGuard nodes.
