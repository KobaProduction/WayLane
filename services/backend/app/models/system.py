from pydantic import BaseModel


class InboundSummary(BaseModel):
    tag: str
    protocol: str
    network: str | None = None


class SystemResourceStats(BaseModel):
    version: str
    uptime_seconds: int
    mem_total: int | None = None
    mem_used: int | None = None
    disk_total: int | None = None
    disk_used: int | None = None
    cpu_cores: int | None = None
    cpu_usage: float | None = None


class SystemSubscriptionsStats(BaseModel):
    total_subscriptions: int
    online_subscriptions: int
    active_subscriptions: int
    on_hold_subscriptions: int
    disabled_subscriptions: int
    expired_subscriptions: int
    limited_subscriptions: int
    incoming_bandwidth: int
    outgoing_bandwidth: int


class SystemStats(SystemResourceStats, SystemSubscriptionsStats):
    pass


class WorkerHealth(BaseModel):
    status: str
    response_time_ms: int | None = None
    error: str | None = None


class WorkersHealth(BaseModel):
    scheduler: WorkerHealth
    node: WorkerHealth


class WireGuardSubnetUsage(BaseModel):
    subnet: str
    interface_tags: list[str]
    capacity: int
    used: int
    free: int
    free_ips: list[str]
