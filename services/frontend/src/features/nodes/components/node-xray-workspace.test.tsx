import { test } from 'node:test'
import assert from 'node:assert/strict'
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { MemoryRouter } from 'react-router'
import { NodeXrayCoreAssignment } from './node-xray-core-assignment'
import { NodeXrayRuntimeControls } from './node-xray-runtime-controls'
import { NodeXrayClientManagement } from './node-xray-client-management'

const noop = () => {}

test('a saved Core is the primary Xray workflow; manual JSON remains optional and collapsed', () => {
  const coreHtml = renderToStaticMarkup(createElement(MemoryRouter, null, createElement(NodeXrayCoreAssignment, {
    cores: [{ id: 7, name: 'Test Core', type: 'xray' }],
    selectedCoreId: '7', current: { configured: true, generation: 9, applied_generation: 8,
      source_core_id: 7, runtime_status: 'running' }, busy: false,
    onCoreChange: noop, onApply: noop, onApplyRouting: noop,
  })))
  assert.match(coreHtml, /Сохранённый Xray Core/)
  assert.match(coreHtml, /Test Core/)
  assert.match(coreHtml, /Назначен в конфигурации/)
  assert.doesNotMatch(coreHtml, /Node подтвердила применение/) // The status belongs to its server-backed status widget.
  assert.match(coreHtml, /Применить профиль/)
  const manual = renderToStaticMarkup(createElement(NodeXrayRuntimeControls, {
    imageVersion: '26.10.21', region: '', configuration: '', configured: true, busy: false,
    onImageVersionChange: noop, onRegionChange: noop, onConfigurationChange: noop,
    onGenerateSample: noop, onCommand: noop, onRefresh: noop,
  }))
  assert.match(manual, /<details[^>]*data-testid="node-xray-advanced-controls"/)
  assert.match(manual, /Ручные операции Xray и JSON/)
  assert.doesNotMatch(manual, /<details[^>]*open/)
})

test('Xray clients never offer a manual sync for subscription-managed inbound', () => {
  const html = renderToStaticMarkup(createElement(NodeXrayClientManagement, {
    inbounds: [{ tag: 'vless', protocol: 'vless', port: 62051, user_count: 0, identities: [] }],
    managedInboundTags: ['vless'], runtime: { configured: true, runtime_status: 'running' },
    inboundTag: 'vless', usersJson: '[{}]', busy: false,
    onInboundTagChange: noop, onUsersJsonChange: noop, onManageInbound: noop, onSyncUsers: noop,
  }))
  assert.match(html, /Управляется подписками/)
  assert.match(html, /Ручная синхронизация пользователей/)
  const disabledSync = /<button[^>]*disabled=""[^>]*>Синхронизировать пользователей<\/button>/
  assert.match(html, disabledSync)
})
