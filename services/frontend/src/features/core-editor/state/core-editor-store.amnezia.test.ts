import { test } from 'node:test'
import assert from 'node:assert/strict'
import { useCoreEditorStore } from './core-editor-store'
import { getWireGuardPersistConfig } from '../kit/wireguard-adapter'
import { isAmneziaDraft, setAmneziaMode } from '../kit/amneziawg-settings'

test('new WireGuard-family cores prefer AWG 3.1 without converting existing configs', () => {
  useCoreEditorStore.getState().initNew('wg')
  let state = useCoreEditorStore.getState()
  assert.equal(state.kind, 'wg')
  assert.ok(state.wgDraft)
  assert.equal(isAmneziaDraft(state.wgDraft!), true)
  const saved = getWireGuardPersistConfig(state.wgDraft!)
  assert.equal(saved.ok, true, JSON.stringify(saved))
  if (saved.ok) assert.equal(saved.config.engine, 'amneziawg')

  state.updateWgDraft(draft => setAmneziaMode(draft, false))
  state = useCoreEditorStore.getState()
  assert.equal(isAmneziaDraft(state.wgDraft!), false)
  assert.equal(state.wgDraft?.extra.amnezia, undefined)
  assert.equal(getWireGuardPersistConfig(state.wgDraft!).ok, true)

  state.switchKind('xray')
  useCoreEditorStore.getState().switchKind('wg')
  assert.equal(isAmneziaDraft(useCoreEditorStore.getState().wgDraft!), true)
})
