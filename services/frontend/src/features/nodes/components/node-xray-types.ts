/** Public Node Xray status and non-secret Core metadata returned by the Backend. */
export type XrayStatus = {
  configured: boolean
  generation?: number
  image_version?: string
  action?: string
  region?: string | null
  runtime_status?: string
  reported_at?: string | null
  applied_generation?: number | null
  source_core_id?: number | null
  last_error?: string | null
}

export type EditorCoreOption = { id: number; name: string; type: string | null }

export type XrayInboundInfo = {
  tag: string
  protocol: string
  port: number
  user_count: number
  identities: string[]
}
