import { test } from 'node:test'
import assert from 'node:assert/strict'
import { createNewWireGuardDraft, draftToPersistedConfig, getWireGuardPersistConfig, wireGuardConfigToDraft } from './wireguard-adapter'
import {
  AMNEZIA_31_PRESET,
  applyAmneziaPreset,
  withAmneziaCorePreset,
  asPlainWireGuardCore,
  getAmneziaSettings,
  isAmneziaDraft,
  setAmneziaMode,
  updateAmneziaSetting,
  validateAmneziaSettings,
} from './amneziawg-settings'

test('WireGuard remains compatible: enabling and disabling AWG preserves interface, key and IPs', () => {
  const wg = createNewWireGuardDraft()
  const awg = setAmneziaMode(wg, true)
  assert.equal(isAmneziaDraft(awg), true)
  assert.deepEqual(getAmneziaSettings(awg), AMNEZIA_31_PRESET)
  const restored = setAmneziaMode(awg, false)
  assert.equal(isAmneziaDraft(restored), false)
  assert.equal(restored.extra.amnezia, undefined)
  assert.equal(restored.extra.engine, undefined)
  assert.deepEqual(draftToPersistedConfig(restored), draftToPersistedConfig(wg))
})

test('AWG 3.1 preset creates schema-compatible payload and survives edit reload', () => {
  const initial = applyAmneziaPreset(createNewWireGuardDraft())
  const enabled = updateAmneziaSetting(initial, 'i1', '<r 4><t>')
  const padded = updateAmneziaSetting(enabled, 'header_protection_key', btoa('k'.repeat(32)))
  const withBoolean = updateAmneziaSetting(padded, 'disable_cookies', true)
  assert.deepEqual(validateAmneziaSettings(withBoolean), [])
  const result = getWireGuardPersistConfig(withBoolean)
  assert.equal(result.ok, true, JSON.stringify(result))
  if (!result.ok) return
  assert.equal(result.config.engine, 'amneziawg')
  assert.deepEqual(result.config.amnezia, withBoolean.extra.amnezia)
  const reload = wireGuardConfigToDraft(result.config)
  assert.equal(reload.ok, true, JSON.stringify(reload))
  if (!reload.ok) return
  assert.deepEqual(draftToPersistedConfig(reload.draft), result.config)
  assert.equal(isAmneziaDraft(reload.draft), true)
})

test('AWG rejects invalid numbers, ranges, overlapping headers and CPS injection before saving', () => {
  const initial = applyAmneziaPreset(createNewWireGuardDraft())
  for (const [key, value] of [
    ['jc', '-1'], ['s4', '65536'], ['h1', '1-abc'],
    ['jmin', '999'], ['h2', '100'], ['i1', '<t>\nprivate_key=hijack'],
    ['header_protection_key', 'invalid'],
  ] as const) {
    const invalid = updateAmneziaSetting(initial, key, value)
    assert.ok(validateAmneziaSettings(invalid).length > 0, key)
    const result = getWireGuardPersistConfig(invalid)
    assert.equal(result.ok, false, key)
  }
})

test('AWG header protection requires S1–S4 >=12 and valid base64, but supports range controls', () => {
  const initial = updateAmneziaSetting(applyAmneziaPreset(createNewWireGuardDraft()), 'header_protection_key', btoa('k'.repeat(32)))
  assert.deepEqual(validateAmneziaSettings(initial), [])
  assert.ok(validateAmneziaSettings(updateAmneziaSetting(initial, 's3', '11')).length > 0)
  assert.deepEqual(validateAmneziaSettings(updateAmneziaSetting(initial, 'h1', '100-105')), [])
})

test('unknown AWG options are rejected; plain WG draft never emits AWG params', () => {
  const wg = createNewWireGuardDraft()
  assert.equal(getWireGuardPersistConfig(wg).ok, true)
  assert.equal(draftToPersistedConfig(wg).amnezia, undefined)
  const awg = applyAmneziaPreset(wg)
  const invalid = { ...awg, extra: { ...awg.extra, amnezia: { ...getAmneziaSettings(awg), unknown: 'bad' } } }
  assert.ok(validateAmneziaSettings(invalid).length > 0)
})


test('AWG is the preferred new core profile; explicit plain WG remains available', () => {
  const preferred = applyAmneziaPreset(createNewWireGuardDraft())
  const saved = getWireGuardPersistConfig(preferred)
  assert.equal(saved.ok, true, JSON.stringify(saved))
  if (!saved.ok) return
  assert.equal(saved.config.engine, 'amneziawg')
  const plain = setAmneziaMode(preferred, false)
  assert.equal(isAmneziaDraft(plain), false)
  assert.equal(getWireGuardPersistConfig(plain).ok, true)
  assert.equal(draftToPersistedConfig(plain).amnezia, undefined)
})


test('JSON modal AWG preset shares editor defaults and plain WG mode preserves keys/peers', () => {
  const original = { private_key: 'synthetic-only', address: ['10.5.0.1/24'], peers: [{ public_key: 'peer-test' }] }
  const awg = withAmneziaCorePreset(original)
  assert.equal(awg.engine, 'amneziawg')
  assert.deepEqual(awg.amnezia, AMNEZIA_31_PRESET)
  assert.deepEqual(original, { private_key: 'synthetic-only', address: ['10.5.0.1/24'], peers: [{ public_key: 'peer-test' }] })
  const plain = asPlainWireGuardCore(awg)
  assert.equal(Object.prototype.hasOwnProperty.call(plain, 'engine'), false)
  assert.equal(Object.prototype.hasOwnProperty.call(plain, 'amnezia'), false)
  assert.deepEqual(plain, original)
})


test('JSON modal AWG transforms reject non-object JSON rather than replacing config', () => {
  for (const invalid of [null, [], 'value']) {
    assert.throws(() => withAmneziaCorePreset(invalid as unknown as Record<string, unknown>), /Expected WireGuard object/)
    assert.throws(() => asPlainWireGuardCore(invalid as unknown as Record<string, unknown>), /Expected WireGuard object/)
  }
})
