import type { NodeWssObservation } from './node-registry-types'

export type WssPresence = 'recent' | 'stale' | 'unknown'

/** The backend observation remains true as of its recorded check time, not a live socket state. */
export function observedWssPresence(
  nodeUuid: string,
  checkedAt: number | null,
  observations: readonly NodeWssObservation[] | null,
): WssPresence {
  if (checkedAt === null || !observations) return 'unknown'
  const row = observations.find(item => item.node_uuid === nodeUuid)
  if (!row) return 'unknown'
  return row.wss_fresh === true ? 'recent' : row.wss_fresh === false ? 'stale' : 'unknown'
}
