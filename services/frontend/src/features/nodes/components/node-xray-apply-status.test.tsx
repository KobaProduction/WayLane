import assert from 'node:assert/strict'
import { test } from 'node:test'
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { createInstance } from 'i18next'
import { I18nextProvider } from 'react-i18next'
import { NodeXrayApplyStatus, xrayApplyState } from './node-xray-apply-status'
import type { XrayStatus } from './node-xray-types'

const base: XrayStatus = { configured: true, generation: 11, applied_generation: 10, runtime_status: 'pending' }

test('Xray application never treats desired generation as an applied result', () => {
  assert.equal(xrayApplyState(undefined), 'unloaded')
  assert.equal(xrayApplyState({ configured: false }), 'unconfigured')
  assert.equal(xrayApplyState(base), 'queued')
  assert.equal(xrayApplyState({ ...base, runtime_status: 'running' }), 'queued')
  assert.equal(xrayApplyState({ ...base, applied_generation: null as unknown as number, runtime_status: 'running' }), 'unknown')
  assert.equal(xrayApplyState({ ...base, applied_generation: 12, runtime_status: 'running' }), 'unknown')
  assert.equal(xrayApplyState({ ...base, applied_generation: 11, runtime_status: 'running' }), 'applied')
  assert.equal(xrayApplyState({ ...base, applied_generation: 11, runtime_status: 'stopped' }), 'stopped')
  assert.equal(xrayApplyState({ ...base, runtime_status: 'error' }), 'error')
  assert.equal(xrayApplyState({ ...base, runtime_status: 'requires_restart' }), 'requires_restart')
})

test('Xray rendered state identifies Node report, not live VPN health', async () => {
  const i18n = createInstance()
  await i18n.init({ lng: 'ru', resources: { ru: { translation: {} } }, initImmediate: false })
  const view = (current: XrayStatus) => renderToStaticMarkup(createElement(I18nextProvider, { i18n }, createElement(NodeXrayApplyStatus, { current })))
  const pending = view(base)
  assert.match(pending, /Ожидается подтверждение Node/)
  assert.match(pending, /Применено: 10 · желаемое: 11/)
  assert.doesNotMatch(pending, /Node подтвердила применение/)
  const applied = view({
    ...base, applied_generation: 11, runtime_status: 'running', reported_at: '2026-11-17T10:00:00Z',
  })
  assert.match(applied, /Node подтвердила применение/)
  assert.match(applied, /Последний отчёт Xray/)
})
