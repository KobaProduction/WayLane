# WayLane deployment — current topology

The active **development** deployment on Coolify Koba / `tambov` is owned
by [deploy/README.md](../../deploy/README.md). That document is canonical
for resource names, installation, network topology, secrets, and acceptance.
Do not use the old two-app proposal or the root all-in-one Compose to modify
the active resources.

- **WayLane / Dependencies** — PostgreSQL 17; manually deployed, never
  restarted merely because an application source file changed.
- **WayLane / Backend** — private `waylane-api:8000`; no public domain,
  private HTTP listener deliberately set via `UVICORN_HOST=0.0.0.0`,
  reached through the private predefined network only.
- **WayLane / Frontend** — public HTTPS `waylane-dev.koba-nexus.ru`,
  Coolify route declared by `SERVICE_URL_FRONTEND_8080: /`;
  Nginx serves UI, proxies `/api/`, `/sub/` and binary WebSocket to Backend.
  Browser diagnostic events go to the authenticated Backend API, which exports
  to SigNoz as `WayLane/frontend`; no Nginx log exporter or collector is used.
  `SERVICE_FQDN_FRONTEND_8080` is **not** an operator input or required
  second public-domain directive.
- **WayLane / Node** — separate, manual-only Coolify runtime on
  `tambov`, sourced from `/deploy/node/discovery` and
  `/docker-compose.yaml` in Raw mode. Bootstrap exited 0; stable non-root
  Agent and nested Node are running/healthy, approved through a real
  Discovery invitation (subsequently revoked). Backend PostgreSQL
  confirmed WSS-delivered `machine_metrics` and recent `last_seen_at`
  both before and after a controlled Agent restart. The Classic profile
  remains an alternative. The Node has no public management domain, no
  system Docker socket and **no applied Xray desired state**. VPN Core
  execution and real client TCP/UDP traffic still require acceptance.

Frontend and Backend deployments are currently manual. Automated Git
deployments, if deliberately activated, can use commits as the trigger; path
filters are optional. No blanket Watch Paths requirement applies to
Dependencies.

Production releases, migration, node runtime and data recovery retain their
own acceptance boundaries. See
[Node deployment](../../deploy/node/README.md) and
[Compose alternatives](../../deploy/compose/README.md).
