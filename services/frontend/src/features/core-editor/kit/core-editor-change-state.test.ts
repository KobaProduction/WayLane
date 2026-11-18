import assert from 'node:assert/strict'
import { test } from 'node:test'
import type { CoreResponse } from '@/service/api'
import { DEFAULT_XRAY_CORE_CONFIG } from '@/lib/default-xray-core-config'
import { useCoreEditorStore } from '../state/core-editor-store'
import { selectCoreEditorHasActualChanges } from './core-editor-change-state'

const savedCore: CoreResponse = {
  id: 1,
  name: 'Local snapshot of a saved Xray Core',
  type: 'xray',
  created_at: '2026-11-17T12:00:00Z',
  config: DEFAULT_XRAY_CORE_CONFIG,
  fallbacks_inbound_tags: [],
  exclude_inbound_tags: [],
}

function currentActualChanges(): boolean {
  return selectCoreEditorHasActualChanges(useCoreEditorStore.getState())
}

test('opening an existing Core and switching sections never invents an unsaved edit', () => {
  useCoreEditorStore.getState().reset()
  useCoreEditorStore.getState().initFromCore(savedCore)
  assert.equal(currentActualChanges(), false)
  assert.equal(useCoreEditorStore.getState().dirty, false)
  assert.equal(useCoreEditorStore.getState().monacoDirty, false)
  for (const section of ['balancers', 'routing', 'inbounds', 'bindings'] as const) {
    useCoreEditorStore.getState().setActiveSection(section)
    assert.equal(currentActualChanges(), false, `No edits while opening ${section}`)
  }
  useCoreEditorStore.getState().initFromCore(savedCore, { preserveNavigation: true })
  assert.equal(currentActualChanges(), false, 'Refetch of the same persisted Core stays clean')
  assert.equal(useCoreEditorStore.getState().serverHydratedConfigJson, JSON.stringify(savedCore.config))
  useCoreEditorStore.getState().reset()
})

test('structural no-op marks transient dirty but does not block REALITY with a false positive', () => {
  useCoreEditorStore.getState().reset()
  useCoreEditorStore.getState().initFromCore(savedCore)
  useCoreEditorStore.getState().updateXrayProfile(profile => ({ ...profile }))
  assert.equal(useCoreEditorStore.getState().dirty, true)
  assert.equal(currentActualChanges(), false, 'Semantic no-op must not appear as actual change')
  useCoreEditorStore.getState().discardDraft()
  assert.equal(useCoreEditorStore.getState().dirty, false)
  assert.equal(currentActualChanges(), false)
  useCoreEditorStore.getState().reset()
})

test('a genuine Core change is detected and reverting the saved value restores clean state', () => {
  useCoreEditorStore.getState().reset()
  useCoreEditorStore.getState().initFromCore(savedCore)
  useCoreEditorStore.getState().setCoreName('Edited, not saved')
  assert.equal(currentActualChanges(), true)
  useCoreEditorStore.getState().setCoreName(savedCore.name)
  assert.equal(currentActualChanges(), false)
  useCoreEditorStore.getState().setMonacoJson('{"testing":"unsaved"}', { dirty: true })
  assert.equal(currentActualChanges(), true)
  useCoreEditorStore.getState().discardDraft()
  assert.equal(currentActualChanges(), false)
  useCoreEditorStore.getState().reset()
})


test('a genuine clean-state server refetch updates the snapshot without generating dirty state', () => {
  useCoreEditorStore.getState().reset()
  useCoreEditorStore.getState().initFromCore(savedCore)
  useCoreEditorStore.getState().setActiveSection('balancers')
  const remoteConfig = JSON.parse(JSON.stringify(savedCore.config)) as Record<string, unknown>
  remoteConfig.log = { loglevel: 'warning' }
  const newerSavedCore = { ...savedCore, config: remoteConfig }
  assert.equal(currentActualChanges(), false)
  useCoreEditorStore.getState().initFromCore(newerSavedCore, { preserveNavigation: true })
  assert.equal(currentActualChanges(), false)
  assert.equal(useCoreEditorStore.getState().activeSection, 'balancers')
  assert.equal(useCoreEditorStore.getState().serverHydratedConfigJson, JSON.stringify(remoteConfig))
  assert.equal(useCoreEditorStore.getState().monacoDirty, false)
  useCoreEditorStore.getState().reset()
})
