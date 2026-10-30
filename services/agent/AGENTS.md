# Stable WayLane Agent

Read root AGENTS.md and services/agent/README.md. The Agent owns one
private rootless Docker daemon and the independently replaceable Node image.
Only the Node owns Panel WSS and manages VPN Cores. Agent local management
uses versioned Unix-only API; never accept arbitrary image references.
Do not add an additional public management endpoint or host Docker socket.
