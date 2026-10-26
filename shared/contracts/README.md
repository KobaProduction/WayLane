# Wire contracts

Backend owns its OpenAPI; node agent owns common/service.proto.
Do not duplicate an owner ORM or implementation in a generic shared library.
Only verified protocol DTOs belong here when consumed by multiple services.
