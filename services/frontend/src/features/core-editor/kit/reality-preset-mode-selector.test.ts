import assert from 'node:assert/strict'
import { test } from 'node:test'
import { confirmedRealityBinding } from '@/features/core-editor/components/xray/reality-preset-mode-selector'

test('only matching backend REALITY binding is recognized in the editor', () => {
  const rows = [
    { kind: 'reality', core_id: 5, inbound_tag: 'a', preset_id: 'reality-5-a' },
    { kind: 'reality', core_id: 6, inbound_tag: 'a', preset_id: 'reality-6-a' },
    { kind: 'vless', core_id: 5, inbound_tag: 'b', preset_id: 'vless-5-b' },
  ]
  assert.equal(confirmedRealityBinding(rows, 5, 'a'), 'reality-5-a')
  assert.equal(confirmedRealityBinding(rows, 6, 'a'), 'reality-6-a')
  assert.equal(confirmedRealityBinding(rows, 5, 'b'), null)
  assert.equal(confirmedRealityBinding(rows, null, 'a'), null)
  assert.equal(confirmedRealityBinding(undefined, 5, 'a'), null)
})
