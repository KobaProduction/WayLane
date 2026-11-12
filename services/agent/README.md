# Stable WayLane Agent v1

The installed Agent owns private RootlessKit/dockerd and the lifecycle of a
separately updatable **Node container**. It has no VPN Core drivers or keys.
The Node owns the single outbound WSS/Protobuf connection to the Panel,
and manages its own VPN Core siblings through the private rootless daemon.

On first installation, Agent builds a local seed Node from a binary bundled
in the installer image; a subsequently published immutable Node image can be
installed independently without restarting Agent. The stable Agent API v1
uses only a private Unix socket at NODE_STATE_DIR/control/agent.sock:
- GET /v1/node: current chosen Node image
- PUT /v1/node/release with JSON api_version=1 and pinned image digest.
No inbound TCP management listener is created. Updates pull before cutover
and attempt rollback to the prior Node image on startup failure.

The Agent and its Node/core children use host networking through rootless
Docker 29.5+. The outer Agent stays UID 1000 (non-root). The inner Node
runs as UID 0 **only inside the private rootless user namespace**, which maps
to the outer Agent's unprivileged UID 1000. This is needed for access to
private 0700 state and Docker socket mounts; `--cap-drop ALL`,
`no-new-privileges`, read-only root filesystem, and an explicit UID-map
and effective-capability startup check are required. Rootful Node launches
fail closed. A Core opens/closes its own UDP/TCP listener, not a static
Docker Compose ports mapping. On Linux, this can expose unprivileged ports
(1024 and above) without an Agent restart; reachability/firewall and
bidirectional VPN traffic still need live acceptance. Docker Desktop host
networking is separately subject to platform settings.

Builds and releases are independent: Agent release tags use agent-v* and Node
release tags use node-v*. The first install bundles a seed Node binary from
the monorepo; later a Node digest from the approved GHCR repository may be
applied without changing the Agent. The current Panel does not yet have a
public Node-release endpoint; Agent local Unix API is the authority.

On restart, the Agent retains its desired Node image, identity volume and
private daemon state. It verifies that the running Agent-owned Node image
matches the persisted release. An unexpected image is replaced only after
staging the selected image; if saving a new release fails, the previous
release is restored, and the update reports an error. Failed rollback is
explicitly reported and never silently counted as success.

Health checks require both the Agent's Unix socket and a running labeled
Node. Approval and migration from the former combined
Agent/Node deployment require an isolated runtime test and rollback backup.
