import type { WireGuardCoreDraft, WireGuardValidationIssue } from '@waylane/wireguard-config-kit'

export type AmneziaField = 'jc' | 'jmin' | 'jmax' | 's1' | 's2' | 's3' | 's4' | 'h1' | 'h2' | 'h3' | 'h4'
  | 'i1' | 'i2' | 'i3' | 'i4' | 'i5' | 'header_protection_key'
  | 'content_padding_addition' | 'rekey_after_time' | 'rekey_timeout'
  | 'reject_after_time' | 'keepalive_timeout' | 'max_handshake_attempts'
  | 'random_trailers' | 'disable_cookies'

type AmneziaValue = string | number | boolean
export type AmneziaSettings = Partial<Record<AmneziaField, AmneziaValue>>

// Obfuscation parameters for a new, independent AWG instance. They are NOT
// compatibility settings for clients that only understand plain WireGuard.
export const AMNEZIA_31_PRESET: AmneziaSettings = {
  jc: 4, jmin: 24, jmax: 48,
  s1: 16, s2: 16, s3: 16, s4: 16,
  h1: '100', h2: '200', h3: '300', h4: '400',
  random_trailers: true,
}

/** The legacy JSON modal and the modern draft editor must use the same
 * AWG 3.1 defaults. Never mutate the source config (including peer keys).
 */
export function withAmneziaCorePreset(config: Record<string, unknown>): Record<string, unknown> {
  if (config === null || typeof config !== 'object' || Array.isArray(config)) throw new Error('Expected WireGuard object')
  return { ...config, engine: 'amneziawg', amnezia: { ...AMNEZIA_31_PRESET } }
}

/** Explicit plain WireGuard compatibility profile; no obfuscation fields. */
export function asPlainWireGuardCore(config: Record<string, unknown>): Record<string, unknown> {
  if (config === null || typeof config !== 'object' || Array.isArray(config)) throw new Error('Expected WireGuard object')
  const { engine: _engine, amnezia: _amnezia, ...plain } = config
  return plain
}

export const AWG_NUMBER_FIELDS = ['jc', 'jmin', 'jmax', 's1', 's2', 's3', 's4'] as const
export const AWG_RANGE_FIELDS = [
  'h1', 'h2', 'h3', 'h4', 'content_padding_addition',
  'rekey_after_time', 'rekey_timeout', 'reject_after_time',
  'keepalive_timeout', 'max_handshake_attempts',
] as const
export const AWG_CPS_FIELDS = ['i1', 'i2', 'i3', 'i4', 'i5'] as const
export const AWG_BOOLEAN_FIELDS = ['random_trailers', 'disable_cookies'] as const
const validFields = new Set<string>([
  ...AWG_NUMBER_FIELDS, ...AWG_RANGE_FIELDS, ...AWG_CPS_FIELDS,
  ...AWG_BOOLEAN_FIELDS, 'header_protection_key',
])
const cpsPattern = /^(?:<t>|<b 0x(?:[a-fA-F0-9]{2})+>|<(?:r|rd|rc) (?:[1-9][0-9]{0,2}|1000)>)+$/

export function isAmneziaDraft(draft: WireGuardCoreDraft): boolean {
  return draft.extra.engine === 'amneziawg'
}

export function getAmneziaSettings(draft: WireGuardCoreDraft): AmneziaSettings {
  const raw = draft.extra.amnezia
  return raw && typeof raw === 'object' && !Array.isArray(raw) ? raw as AmneziaSettings : {}
}

export function setAmneziaMode(draft: WireGuardCoreDraft, enabled: boolean): WireGuardCoreDraft {
  const extra = { ...draft.extra }
  if (enabled) {
    extra.engine = 'amneziawg'
    if (!extra.amnezia || typeof extra.amnezia !== 'object' || Array.isArray(extra.amnezia)) {
      extra.amnezia = { ...AMNEZIA_31_PRESET }
    }
  } else {
    delete extra.engine
    delete extra.amnezia
  }
  return { ...draft, extra }
}

export function applyAmneziaPreset(draft: WireGuardCoreDraft): WireGuardCoreDraft {
  return { ...draft, extra: { ...draft.extra, engine: 'amneziawg', amnezia: { ...AMNEZIA_31_PRESET } } }
}

export function updateAmneziaSetting(
  draft: WireGuardCoreDraft, field: AmneziaField, input: string | boolean,
): WireGuardCoreDraft {
  const settings: AmneziaSettings = { ...getAmneziaSettings(draft) }
  if (typeof input === 'boolean') {
    if (input) settings[field] = true
    else delete settings[field]
  } else if (!input.trim()) {
    delete settings[field]
  } else if ((AWG_NUMBER_FIELDS as readonly string[]).includes(field)) {
    const parsed = Number(input)
    settings[field] = input.match(/^\d+$/) && Number.isSafeInteger(parsed) ? parsed : input
  } else {
    settings[field] = input
  }
  return { ...draft, extra: { ...draft.extra, engine: 'amneziawg', amnezia: settings } }
}

function integerRange(value: unknown, max: number): [number, number] | null {
  if (typeof value !== 'string' && typeof value !== 'number') return null
  const parts = String(value).split('-')
  if (parts.length > 2 || parts.some(p => !/^\d+$/.test(p))) return null
  const first = Number(parts[0])
  const last = Number(parts[parts.length - 1])
  return Number.isSafeInteger(first) && Number.isSafeInteger(last) && first <= last && last <= max ? [first, last] : null
}

function isValidHeaderKey(value: string): boolean {
  if (!/^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/.test(value)) return false
  try {
    const raw = atob(value)
    return raw.length === 32 && btoa(raw) === value
  } catch {
    return false
  }
}

export function validateAmneziaSettings(draft: WireGuardCoreDraft): WireGuardValidationIssue[] {
  if (!isAmneziaDraft(draft)) {
    if (draft.extra.amnezia !== undefined || (draft.extra.engine !== undefined && draft.extra.engine !== 'wireguard')) {
      return [{ path: '/engine', code: 'AWG_ENGINE_INVALID', message: 'Amnezia settings require engine=amneziawg.' }]
    }
    return []
  }
  const settings = getAmneziaSettings(draft)
  const errors: WireGuardValidationIssue[] = []
  const add = (field: string, message: string) => errors.push({ path: '/amnezia/' + field, code: 'AWG_OPTION_INVALID', message })
  if (!draft.extra.amnezia || typeof draft.extra.amnezia !== 'object' || Array.isArray(draft.extra.amnezia)) {
    add('amnezia', 'Amnezia settings must be an object.')
    return errors
  }
  for (const field of Object.keys(settings)) if (!validFields.has(field)) add(field, 'Unknown AmneziaWG option.')
  for (const field of AWG_NUMBER_FIELDS) {
    const value = settings[field]
    if (value !== undefined && (!Number.isInteger(value) || Number(value) < 0 || Number(value) > 65535)) {
      add(field, 'Enter a whole number from 0 to 65535.')
    }
  }
  for (const field of AWG_RANGE_FIELDS) {
    const value = settings[field]
    const max = field.startsWith('h') ? 4294967295 : 65535
    if (value !== undefined && !integerRange(value, max)) add(field, 'Enter a non-negative integer or min-max range.')
  }
  const ranges: { field: string; start: number; end: number }[] = []
  for (const [i, field] of (['h1', 'h2', 'h3', 'h4'] as const).entries()) {
    const value = settings[field]
    if (value === undefined) continue
    const range = integerRange(value, 4294967295)
    if (!range || (range[0] === i + 1 && range[1] === i + 1)) continue
    for (const previous of ranges) {
      if (range[0] <= previous.end && previous.start <= range[1]) add(field, 'Header ranges must not overlap.')
    }
    ranges.push({ field, start: range[0], end: range[1] })
  }
  if (Number(settings.jc ?? 0) > 0 && Number(settings.jmin ?? 0) > Number(settings.jmax ?? 0)) {
    add('jmin', 'Jmin cannot exceed Jmax when Jc is enabled.')
  }
  for (const field of AWG_CPS_FIELDS) {
    const value = settings[field]
    if (value !== undefined && (typeof value !== 'string' || value.length > 4096 || !cpsPattern.test(value))) {
      add(field, 'Use supported CPS packet tags, such as <t>, <r 4>, or <b 0xfaab>.')
    }
  }
  const key = settings.header_protection_key
  if (key !== undefined && (typeof key !== 'string' || !isValidHeaderKey(key))) {
    add('header_protection_key', 'Use a base64-encoded 32-byte header protection key.')
  }
  if (typeof key === 'string' && key && isValidHeaderKey(key)) {
    for (const field of ['s1', 's2', 's3', 's4'] as const) {
      if (Number(settings[field] ?? 0) < 12) add(field, 'Padding must be at least 12 with header protection.')
    }
  }
  for (const field of AWG_BOOLEAN_FIELDS) {
    if (settings[field] !== undefined && typeof settings[field] !== 'boolean') add(field, 'Expected a boolean.')
  }
  return errors
}
