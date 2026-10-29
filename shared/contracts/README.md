# Wire contracts

Backend owns its OpenAPI; node agent owns common/service.proto.
Do not duplicate an owner ORM or implementation in a generic shared library.
Only verified protocol DTOs belong here when consumed by multiple services.

The proposed transport-neutral Node/Agent control protocol is described in
[AGENT_CONTROL.md](AGENT_CONTROL.md). It is an architecture contract, not a
shipped gRPC/HTTP-stream/WebSocket wire DTO. Implementations must pass their
own transport interoperability and authorization tests before being accepted.

Agent management is binary Protobuf over gRPC bidi-stream or binary WS only.
The authoritative v1 schema is proto/waylane/control/v1/control.proto;
see AGENT_CONTROL.md for security, lifecycle and transport settings.
