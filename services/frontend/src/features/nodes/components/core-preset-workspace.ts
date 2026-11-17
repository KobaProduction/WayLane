export type CorePresetKind = 'reality' | 'vless' | 'routing'
export type CorePresetTab = 'catalog' | 'details' | 'editor' | 'bindings'

export function validCorePresetKind(value: string | null): CorePresetKind {
  return value === 'vless' || value === 'routing' ? value : 'reality'
}

export function validCorePresetTab(value: string | null): CorePresetTab {
  return value === 'details' || value === 'editor' || value === 'bindings' ? value : 'catalog'
}

export function newPresetFields(kind: CorePresetKind): Record<string, string> {
  return kind === 'reality'
    ? { private_key: '', public_key: '', short_ids: '', mldsa65_seed: '', mldsa65_verify: '' }
    : kind === 'vless'
      ? { decryption: '', encryption: '' }
      : { routing: '{\n  "domainStrategy": "AsIs",\n  "rules": []\n}' }
}

/** Secret inputs start empty, even for an existing preset whose public metadata is visible. */
export function safeExistingPresetFields(kind: CorePresetKind, publicData: Record<string, unknown>): Record<string, string> {
  const fresh = newPresetFields(kind)
  if (kind === 'routing' && publicData.routing && typeof publicData.routing === 'object') {
    fresh.routing = JSON.stringify(publicData.routing, null, 2)
  }
  return fresh
}

export function hasNewPresetMaterial(kind: CorePresetKind, fields: Record<string, string>): boolean {
  if (kind === 'reality') return Boolean(fields.private_key?.trim() && fields.short_ids?.trim())
  if (kind === 'vless') return Boolean(fields.decryption?.trim() && fields.encryption?.trim())
  try {
    const routing = JSON.parse(fields.routing || 'null')
    return routing !== null && typeof routing === 'object' && !Array.isArray(routing) && Array.isArray(routing.rules)
  } catch { return false }
}

export function presetPublicInfo(kind: CorePresetKind, data: Record<string, unknown>): Array<{ label: string; value: string }> {
  if (kind === 'reality') {
    return [
      { label: 'Публичный X25519', value: typeof data.publicKey === 'string' ? data.publicKey : '—' },
      { label: 'Short IDs', value: Array.isArray(data.shortIds) ? data.shortIds.join(', ') : '—' },
      { label: 'ML-DSA65 verify', value: typeof data.mldsa65Verify === 'string' ? data.mldsa65Verify : '—' },
    ]
  }
  if (kind === 'vless') return [{ label: 'VLESS Encryption (клиент)', value: typeof data.encryption === 'string' ? data.encryption : '—' }]
  return [{ label: 'Routing JSON (без приватных ключей)', value: JSON.stringify(data.routing ?? {}, null, 2) }]
}


/** Only warn when switching to another record/type would actually erase an edited draft. */
export function presetDraftChanged(
  kind: CorePresetKind,
  name: string,
  fields: Record<string, string>,
  selected?: { name: string; data: Record<string, unknown> },
): boolean {
  const initial = selected ? safeExistingPresetFields(kind, selected.data) : newPresetFields(kind)
  if (name !== (selected?.name ?? '')) return true
  return Object.keys(initial).some(key => (fields[key] ?? '') !== (initial[key] ?? ''))
}
