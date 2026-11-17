import { test } from 'node:test'
import assert from 'node:assert/strict'
import { validCorePresetKind, validCorePresetTab, newPresetFields, safeExistingPresetFields, hasNewPresetMaterial, presetPublicInfo, presetDraftChanged } from './core-preset-workspace'

test('Xray preset routes fall back to the catalog and supported kinds', () => {
  assert.equal(validCorePresetKind('reality'), 'reality')
  assert.equal(validCorePresetKind('vless'), 'vless')
  assert.equal(validCorePresetKind('routing'), 'routing')
  assert.equal(validCorePresetKind('unknown'), 'reality')
  assert.equal(validCorePresetTab('bindings'), 'bindings')
  assert.equal(validCorePresetTab('details'), 'details')
  assert.equal(validCorePresetTab('editor'), 'editor')
  assert.equal(validCorePresetTab('none'), 'catalog')
})

test('existing private keys cannot be rehydrated from public summaries', () => {
  const reality = safeExistingPresetFields('reality', { publicKey: 'PUBLIC-SYNTHETIC', shortIds: ['aa'] })
  assert.equal(reality.private_key, '')
  assert.equal(reality.mldsa65_seed, '')
  assert.equal(reality.public_key, '')
  assert.equal(reality.short_ids, '')
  const vless = safeExistingPresetFields('vless', { encryption: 'CLIENT-PUBLIC' })
  assert.equal(vless.decryption, '')
  assert.equal(vless.encryption, '')
  assert.deepEqual(newPresetFields('reality').short_ids, '', 'new REALITY keys need explicit random Short ID')
})

test('create/rotate actions need new secrets and route JSON while rename does not', () => {
  assert.equal(hasNewPresetMaterial('reality', { private_key: '', short_ids: 'aa' }), false)
  assert.equal(hasNewPresetMaterial('reality', { private_key: 'fake-secret', short_ids: 'aa' }), true)
  assert.equal(hasNewPresetMaterial('vless', { decryption: 'server', encryption: '' }), false)
  assert.equal(hasNewPresetMaterial('routing', { routing: '{"rules":[]}' }), true)
  assert.equal(hasNewPresetMaterial('routing', { routing: '{bad}' }), false)
})

test('public metadata does not render private fields', () => {
  const fields = presetPublicInfo('reality', { privateKey: 'SECRET-NOT-FOR-BROWSER', publicKey: 'public-qa', shortIds: ['bb'] })
  assert.deepEqual(fields.map(entry => entry.label), ['Публичный X25519', 'Short IDs', 'ML-DSA65 verify'])
  assert.equal(JSON.stringify(fields).includes('SECRET-NOT-FOR-BROWSER'), false)
})


test('discard confirmation is needed only for edited name or new secret values', () => {
  const existing = { name: 'Safe', data: { publicKey: 'PUBLIC', shortIds: ['aa'] } }
  assert.equal(presetDraftChanged('reality', 'Safe', safeExistingPresetFields('reality', existing.data), existing), false)
  assert.equal(presetDraftChanged('reality', 'Safe renamed', safeExistingPresetFields('reality', existing.data), existing), true)
  assert.equal(presetDraftChanged('reality', 'Safe', { ...newPresetFields('reality'), private_key: 'not-shared' }, existing), true)
  assert.equal(presetDraftChanged('reality', '', newPresetFields('reality')), false)
  assert.equal(presetDraftChanged('reality', 'New', newPresetFields('reality')), true)
})
