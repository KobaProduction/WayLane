import type { RegistrationStatus } from './node-registry-overview'

export type NodeRuntimeMetrics = {
  scope: 'agent-runtime'
  hostname: string
  os: string
  arch: string
  cpu_logical_count: number
  memory_total_bytes: number
  memory_available_bytes: number
  disk_total_bytes: number
  disk_free_bytes: number
  load_1: number
  uptime_seconds: number
}

export type NodeRegistration = {
  node_uuid: string
  connection_mode: 'classic' | 'discovery'
  display_name: string
  status: RegistrationStatus
  node_version: string
  node_image: string | null
  observed_ip: string | null
  reported_ipv4: string | null
  reported_ipv6: string | null
  capabilities: string[]
  last_seen_at: string | null
  machine_metrics: NodeRuntimeMetrics | null
}

/** The backend reports a recent heartbeat, not a live WSS socket inspection. */
export type NodeWssObservation = {
  node_uuid: string
  wss_fresh: boolean
  last_seen_at: string | null
}

export type NodeWssSummary = {
  nodes: NodeWssObservation[]
  fresh_wss_nodes: number
}
