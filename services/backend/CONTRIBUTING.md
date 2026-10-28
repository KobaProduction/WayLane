# Contributing to WayLane

**Project owner:** Intens. Primary repository: https://github.com/KobaProduction/WayLane/tree/main/services/backend

WayLane is an open-source project derived from upstream PasarGuard. Preserve attribution, the AGPL-3.0/GPL-3.0 notices and upstream license terms.

## Before making changes

- Read `AGENTS.md`, `README.md` and `UPSTREAM.md` in the repository you are changing.
- Track issues in `KobaProduction/WayLane` with the appropriate `services/backend`, `services/frontend` or `services/node` owner. Changes may be committed directly to `main` when repository permissions allow; a Pull Request is not mandatory.
- Keep public HTTP, node gRPC/REST, subscription-delivery and persistent-state compatibility unless a separately documented migration is approved.
- Never commit secrets, exported user data, certificates, production database dumps or live node configuration.
- Document current functionality and separate it from planned functionality.
- Run checks appropriate to the affected subsystem; describe the checks actually performed in the MR.

## Repository boundaries

- Backend: FastAPI, permissions, node coordination, upstream-compatible subscription-rendering HTML until Vue replacement is ready.
- Frontend: independent React build during transition; target Vue 3 and subscriber UI.
- Node: Go, Xray/WireGuard runtime, future switchable cores.

The public Intens commercial subscription gateway remains in `vpn-core`. CDN, anti-DDoS and a future VPN client are separate concerns.
