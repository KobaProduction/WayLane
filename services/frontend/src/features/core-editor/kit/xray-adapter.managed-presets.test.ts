import assert from 'node:assert/strict'
import { test } from 'node:test'

import {
  getXrayStrictCompileBlockers,
  importRawToProfile,
  profileToPersistedConfig,
  validateProfileForPersist,
  type ManagedCorePresetBinding,
} from '@/features/core-editor/kit/xray-adapter'

const REALITY_PRESET = '11111111-1111-4111-8111-111111111111'
const VLESS_PRESET = '22222222-2222-4222-8222-222222222222'
const ROUTING_PRESET = '33333333-3333-4333-8333-333333333333'
const PUBLIC_KEY = 'AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA' // synthetic 32-byte key, not a live credential
const DIRECT = { protocol: 'freedom', tag: 'direct' }

const realitySource = () => ({
  inbounds: [{
    tag: 'wl-reality-google-44389',
    listen: '0.0.0.0',
    port: 62051,
    protocol: 'vless',
    settings: { clients: [], decryption: 'none' },
    streamSettings: {
      network: 'tcp', security: 'reality',
      realitySettings: {
        dest: 'www.google.com:443', serverNames: ['www.google.com'],
        shortIds: ['1234abcd'], publicKey: PUBLIC_KEY, waylanePresetId: REALITY_PRESET,
      },
    },
  }],
  outbounds: [DIRECT],
})

const realityBinding: ManagedCorePresetBinding = {
  kind: 'reality', inbound_tag: 'wl-reality-google-44389', preset_id: REALITY_PRESET,
}

function issueCodes(profile: ReturnType<typeof importRawToProfile>['profile'], bindings: ManagedCorePresetBinding[] = []) {
  const result = validateProfileForPersist(profile, bindings)
  return result.ok ? [] : [...result.strictBlockers, ...result.coreKitIssues].map(issue => issue.code)
}

test('managed REALITY public projection does not require its encrypted private key in Core Editor', () => {
  const source = realitySource()
  const { profile } = importRawToProfile(source)
  const config = profileToPersistedConfig(profile)
  const reality = (config.inbounds as Record<string, unknown>[])[0].streamSettings as { realitySettings: Record<string, unknown> }
  assert.equal(reality.realitySettings.privateKey, undefined)
  assert.equal(reality.realitySettings.waylanePresetId, REALITY_PRESET)
  assert.equal(reality.realitySettings.publicKey, PUBLIC_KEY)
  assert.ok(issueCodes(profile).includes('XCK_SEMANTIC_INVALID_REALITY_PRIVATE_KEY'))
  assert.ok(issueCodes(profile).includes('XCK_XRAY_STRICT_UNKNOWN_FIELD'))
  assert.deepEqual(getXrayStrictCompileBlockers(profile, [realityBinding]), [])
  assert.deepEqual(issueCodes(profile, [realityBinding]), [])
  const accepted = validateProfileForPersist(profile, [realityBinding])
  assert.equal(accepted.ok, true)
  if (accepted.ok) assert.deepEqual(accepted.config, config) // validator-only key must never enter persisted JSON
})

test('stale or forged managed REALITY references cannot silence validation errors', () => {
  const { profile } = importRawToProfile(realitySource())
  for (const binding of [
    { ...realityBinding, preset_id: VLESS_PRESET },
    { ...realityBinding, inbound_tag: 'other-inbound' },
    { ...realityBinding, kind: 'vless' as const },
  ]) {
    const codes = issueCodes(profile, [binding])
    assert.ok(codes.includes('XCK_SEMANTIC_INVALID_REALITY_PRIVATE_KEY'))
    assert.ok(codes.includes('XCK_XRAY_STRICT_UNKNOWN_FIELD'))
  }
  const unbound = realitySource()
  delete (unbound.inbounds[0].streamSettings.realitySettings as Record<string, unknown>).waylanePresetId
  const { profile: manual } = importRawToProfile(unbound)
  assert.ok(issueCodes(manual, [realityBinding]).includes('XCK_SEMANTIC_INVALID_REALITY_PRIVATE_KEY'))
})

test('confirmed managed REALITY does not mask malformed short IDs or other genuine validation errors', () => {
  const bad = realitySource()
  bad.inbounds[0].streamSettings.realitySettings.shortIds = ['not-hex']
  const { profile } = importRawToProfile(bad)
  assert.ok(issueCodes(profile, [realityBinding]).includes('XCK_SEMANTIC_INVALID_REALITY_SHORT_ID'))
})

test('managed VLESS encryption and routing metadata are excluded from Xray validation only', () => {
  const source = {
    inbounds: [{ tag: 'encrypted', port: 62052, protocol: 'vless', settings: {
      clients: [], decryption: 'none', encryption: 'mlkem768x25519plus.native.0rtt.synthetic', waylanePresetId: VLESS_PRESET,
    }, streamSettings: { network: 'tcp', security: 'none' } }],
    outbounds: [DIRECT],
    routing: { domainStrategy: 'AsIs', rules: [] as unknown[], waylanePresetId: ROUTING_PRESET },
  }
  const { profile } = importRawToProfile(source)
  const bindings: ManagedCorePresetBinding[] = [
    { kind: 'vless', inbound_tag: 'encrypted', preset_id: VLESS_PRESET },
    { kind: 'routing', inbound_tag: '__routing__', preset_id: ROUTING_PRESET },
  ]
  assert.ok(issueCodes(profile).includes('XCK_XRAY_STRICT_UNKNOWN_FIELD'))
  assert.deepEqual(issueCodes(profile, bindings), [])
  assert.deepEqual(profileToPersistedConfig(profile), source)
})
