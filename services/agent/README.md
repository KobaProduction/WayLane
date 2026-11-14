# Stable WayLane Agent v1

The installed Agent owns private RootlessKit/dockerd and the lifecycle of a
separately updatable **Node container**. It has no VPN Core drivers or keys.
The Node owns the single outbound WSS/Protobuf connection to the Panel,
and manages its own VPN Core siblings through the private rootless daemon.

On first installation, Agent builds a local seed Node from a binary bundled
in the installer image. On Agent restart the local seed image is rebuilt from
its embedded binary (using Docker build cache) and its immutable image ID is
compared to the running child; a stale child is replaced without touching
identity volumes. A child stuck in Docker's `restarting` state is not
considered ready. A subsequently published immutable Node image can be
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


### Recovering safely from stale Unix runtime files

The Agent uses two independent persistent volumes: `agent-state` for identity,
Node release selection and its private management socket, and `rootless-run`
for the private Docker/containerd runtime. After a non-graceful exit, those
volumes may retain a dead `containerd.pid`, Unix sockets or `control/agent.sock`.
A stale containerd PID from RootlessKit's previous PID namespace is *not*
proof that the process is alive in a fresh outer container: probing the
recorded PID via the outer PID namespace can be misleading.

At each **startup only**, the Agent verifies private directory ownership,
file types, Unix socket ownership and actual listener state before reclaiming
any orphaned runtime PID/socket. An active listener, unexpected symlink,
nonprivate directory or other uncertain condition aborts startup rather than
unlinking a running service. Persistent Docker images/containers, the Node
identity, pinned release and Xray config are **not** removed. A successful
recovery is logged without disclosing credentials or filesystem secrets.
The Agent's only private Unix management API remains unchanged; no second
Docker daemon or host Docker socket is introduced.

### Operator-controlled inner Node releases (stable outer Agent)

The Agent is **not** a self-updater and must not be recreated by ordinary
Node/Xray/Core releases. Updates are explicit operator commands from Node
Registry via the **existing** authenticated Node→Backend binary WSS. The
replaceable Node forwards the approved immutable
`ghcr.io/kobaproduction/waylane-node@sha256:<digest>` to the stable Agent
using its private, mode-0600 Unix socket. There is no public update port,
host Docker socket, or direct Backend→Docker connection.

`PUT /v1/node/release` acknowledges a UUID-scoped operation immediately
(HTTP 202). `GET /v1/node/release` returns a sanitized state with phases
`queued → pulling → replacing → awaiting_wss → completed`, progress percentage,
previous image and controlled error codes. Percentages reflect defined
lifecycle milestones rather than Docker download-byte measurements. The Agent persistently journals the
operation in its existing private `agent-state` volume. It **pulls the new
image before stopping the previous Node**, then requires the replacement to
authenticate to Backend WSS and acknowledge that control session through
`POST /v1/node/release/ready`. On timeout it restores the prior pinned Node;
on interruption during Agent restart it reconciles the previous version,
then requires an authenticated WSS confirmation from that previous image
before reporting a completed rollback. Unconfirmed recovery is explicitly
`rollback_unverified`, never silently accepted. Successful local Docker `run` is never
reported as successful WSS deployment. Operator status can be inspected via
Node Registry without waiting for a synchronous HTTP connection from a
container that is about to be replaced.

An operator can also explicitly request a WSS reconnect through Node Registry;
that **does not** restart Agent, Node, Xray or any other VPN Core. No automatic
release polling or unattended updates run between operator requests.

Agent auto-bootstrap from its bundled seed occurs only when the Node child is
missing or stopped. A healthy seed child **is not replaced** merely because
the outer Agent image was manually updated. The installed Agent/Compose upgrade
remains a separate rare operator-controlled operation.

Unexpected Node/Core process crashes now have a bounded three-retry restart
policy instead of indefinitely looping on a broken release; the existing
healthcheck and user-visible operation state distinguish runtime failures.
