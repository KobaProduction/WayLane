/** A rule must select one effective action. Selecting a new action clears the old one. */
export function routingActionTargetPatch(field: string, value: unknown): Record<string, unknown> {
  const patch: Record<string, unknown> = { [field]: value }
  if (field === 'outboundTag' && typeof value === 'string' && value.trim()) patch.balancerTag = undefined
  if (field === 'balancerTag' && typeof value === 'string' && value.trim()) patch.outboundTag = undefined
  return patch
}
