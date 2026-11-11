# Historical deployment plan (superseded)

This proposal predates the independently deployed WayLane application stack
on Koba `tambov`. **Do not operate the current deployment from this plan.**

Current deployment ownership, secrets, server network, service names, and
acceptance: [deploy/README.md](../../../../deploy/README.md).
Current public-route contract: `SERVICE_URL_FRONTEND_8080: /` on Frontend;
Backend remains internal on port 8000. The current deployment uses separate
Dependencies, Backend and Frontend resources, rather than a grouped database.
Dependencies are manually deployed; application Watch Paths are optional.
