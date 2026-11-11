# WayLane deployment — current topology

The active **development** deployment on Coolify Koba / `tambov` is owned
by [deploy/README.md](../../deploy/README.md). That document is canonical
for resource names, installation, network topology, secrets, and acceptance.
Do not use the old two-app proposal or the root all-in-one Compose to modify
these three running resources.

- **WayLane / Dependencies** — PostgreSQL 17; manually deployed, never
  restarted merely because an application source file changed.
- **WayLane / Backend** — private `waylane-api:8000`; no public domain,
  private HTTP listener deliberately set via `UVICORN_HOST=0.0.0.0`,
  reached through the private predefined network only.
- **WayLane / Frontend** — public HTTPS `waylane-dev.koba-nexus.ru`,
  Coolify route declared by `SERVICE_URL_FRONTEND_8080: /`;
  Nginx serves UI, proxies `/api/`, `/sub/` and binary WebSocket to Backend.
  `SERVICE_FQDN_FRONTEND_8080` is **not** an operator input or required
  second public-domain directive.
- **WayLane Node** — separate future enrollment and VPN-core pilot; no
  production traffic accepted by the application healthchecks alone.

Frontend and Backend deployments are currently manual. Automated Git
deployments, if deliberately activated, can use commits as the trigger; path
filters are optional. No blanket Watch Paths requirement applies to
Dependencies.

Production releases, migration, node runtime and data recovery retain their
own acceptance boundaries. See
[Node runtime](../../services/node/agent/README.md) and
[Compose alternatives](../../deploy/compose/README.md).
