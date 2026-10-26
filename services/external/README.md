# External services — independent ownership

PostgreSQL, public ingress/API balancing, SigNoz and Coolify are not owned by
the WayLane application images. Deploy, back up, and upgrade them in approved
infrastructure scopes. Never reuse production DB volumes, Doctor Dental
credentials or privileged nodes inside the API/UI stack.

Before generating deployment Compose ENV or creating Coolify resources, approve
the exact operator-variable names, Required/default flags, secret scopes,
domain generation, connected Docker networks and rollback contract.
