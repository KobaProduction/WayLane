export type RealityBinding = { core_id: number; inbound_tag: string; kind: string; preset_id: string }

export function confirmedRealityBinding(data: unknown, coreId: number | null, tag: string): string | null {
  if (coreId === null || !Array.isArray(data)) return null
  const row = (data as RealityBinding[]).find(item =>
    item && item.core_id === coreId && item.inbound_tag === tag && item.kind === 'reality' && typeof item.preset_id === 'string',
  )
  return row?.preset_id ?? null
}


/** Preserve the confirmed selection when Radix reinitializes its async option list. */
export function effectiveRealityPresetSelection(selected: string, binding: string | null): string {
  return selected || binding || ''
}
