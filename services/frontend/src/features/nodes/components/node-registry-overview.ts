/** Registration is an authorization status, not proof of a live WSS connection. */
export type RegistrationStatus = 'pending' | 'approved' | 'revoked'
export type RegistrationFilter = RegistrationStatus | 'all'

export function registrationCounts(nodes: readonly { status: RegistrationStatus }[]) {
  const counts = { all: nodes.length, approved: 0, pending: 0, revoked: 0 }
  for (const node of nodes) counts[node.status] += 1
  return counts
}

export function filterByRegistration<T extends { status: RegistrationStatus }>(nodes: readonly T[], filter: RegistrationFilter): T[] {
  return filter === 'all' ? [...nodes] : nodes.filter(node => node.status === filter)
}

/** URL filters are explicit and survive reload/back navigation; unknown values fail closed to all. */
export function validRegistrationFilter(value: string | null): RegistrationFilter {
  return value === 'approved' || value === 'pending' || value === 'revoked' ? value : 'all'
}
