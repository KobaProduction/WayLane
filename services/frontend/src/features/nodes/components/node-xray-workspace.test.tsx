import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { createInstance } from 'i18next'
import { I18nextProvider } from 'react-i18next'
import { MemoryRouter } from 'react-router'
import { NodeXrayCoreAssignment } from './node-xray-core-assignment'
import { NodeXrayRuntimeControls } from './node-xray-runtime-controls'
import { NodeXrayClientManagement } from './node-xray-client-management'

const noop = () => {}
type ClientsProps = Parameters<typeof NodeXrayClientManagement>[0]

async function renderClients(props: ClientsProps, language = 'ru') {
  const resources = JSON.parse(readFileSync('public/statics/locales/' + language + '.json', 'utf8'))
  const i18n = createInstance()
  await i18n.init({ lng: language, resources: { [language]: { translation: resources } }, initImmediate: false, interpolation: { escapeValue: false } })
  return renderToStaticMarkup(createElement(I18nextProvider, { i18n }, createElement(NodeXrayClientManagement, props)))
}

test('a saved Core is the primary Xray workflow; manual JSON remains optional and collapsed', () => {
  const coreHtml = renderToStaticMarkup(createElement(MemoryRouter, null, createElement(NodeXrayCoreAssignment, {
    cores: [{ id: 7, name: 'Test Core', type: 'xray' }],
    selectedCoreId: '7', current: { configured: true, generation: 9, applied_generation: 8,
      source_core_id: 7, runtime_status: 'running' }, busy: false,
    onCoreChange: noop, onApply: noop, onApplyRouting: noop,
  })))
  assert.match(coreHtml, /Конфигурация Xray/)
  assert.match(coreHtml, /Test Core/)
  assert.match(coreHtml, /Выбран для Node:/)
  assert.doesNotMatch(coreHtml, /Node подтвердила применение/)
  assert.match(coreHtml, /Применить профиль/)
  assert.match(coreHtml, /<select[^>]*w-full[^>]*shrink-0[^>]*sm:shrink/, 'Selected Core and actions must not shrink the mobile select')
  const manual = renderToStaticMarkup(createElement(NodeXrayRuntimeControls, {
    imageVersion: '26.10.21', region: '', configuration: '', configured: true, busy: false,
    onImageVersionChange: noop, onRegionChange: noop, onConfigurationChange: noop,
    onGenerateSample: noop, onCommand: noop, onRefresh: noop,
  }))
  assert.match(manual, /<details[^>]*data-testid="node-xray-advanced-controls"/)
  assert.match(manual, /Ручные операции Xray и JSON/)
  assert.doesNotMatch(manual, /<details[^>]*open/)
})

test('subscription-managed inbounds stay read-only by default; dangerous switches are behind closed maintenance', async () => {
  const html = await renderClients({
    inbounds: [
      { tag: 'reality-live', protocol: 'vless', port: 62055, user_count: 0, identities: [] },
      { tag: 'manual-test', protocol: 'socks', port: 18443, user_count: 1, identities: [] },
    ],
    managedInboundTags: ['reality-live'], runtime: { configured: true, runtime_status: 'running' },
    inboundTag: 'reality-live', usersJson: '[{}]', busy: false,
    onInboundTagChange: noop, onUsersJsonChange: noop, onManageInbound: noop, onSyncUsers: noop,
  })
  const [ordinaryView, advancedMarkup] = html.split('data-testid="node-xray-client-maintenance"')
  assert.match(ordinaryView, /Входящие подключения/)
  assert.match(ordinaryView, /Клиенты из подписок/)
  assert.match(ordinaryView, /VLESS · 62055/)
  assert.match(ordinaryView, /Ручных клиентов: 1/)
  assert.doesNotMatch(ordinaryView, /Отключить синхронизацию/)
  assert.match(advancedMarkup, /Расширенное управление клиентами/)
  assert.match(advancedMarkup, /Отключить синхронизацию/)
  assert.match(advancedMarkup, /aria-haspopup="dialog"/)
  assert.doesNotMatch(advancedMarkup.slice(0, 120), /open=""/)
  assert.match(advancedMarkup, /<option value="manual-test">/)
  assert.doesNotMatch(advancedMarkup, /<option value="reality-live">/)
  const disabledSync = /<button[^>]*disabled=""[^>]*>Отправить список клиентов<\/button>/
  assert.match(html, disabledSync)
})

test('manual inbound may be selected without offering a subscription-managed inbound as a manual replacement target', async () => {
  const html = await renderClients({
    inbounds: [
      { tag: 'managed', protocol: 'trojan', port: 62055, user_count: 0, identities: [] },
      { tag: 'manual', protocol: 'vless', port: 18445, user_count: 2, identities: [] },
      { tag: 'unsupported', protocol: 'hysteria', port: 18050, user_count: 0, identities: [] },
    ],
    managedInboundTags: ['managed'], runtime: { configured: true, runtime_status: 'running' },
    inboundTag: 'manual', usersJson: '[{}]', busy: false,
    onInboundTagChange: noop, onUsersJsonChange: noop, onManageInbound: noop, onSyncUsers: noop,
  })
  assert.match(html, /<option value="manual" selected="">/)
  assert.doesNotMatch(html, /<option value="managed"/)
  assert.doesNotMatch(html, /<option value="unsupported"/)
  assert.match(html, /Отправить список клиентов<\/button>/)
  assert.doesNotMatch(html, /<button[^>]*disabled=""[^>]*>Отправить список клиентов<\/button>/)
})

test('unknown subscription ownership fails closed instead of exposing manual-mode actions', async () => {
  const html = await renderClients({
    inbounds: [{ tag: 'possibly-managed', protocol: 'vless', port: 62055, user_count: 0, identities: [] }],
    managedInboundTags: undefined,
    runtime: { configured: true, runtime_status: 'running' },
    inboundTag: 'possibly-managed', usersJson: '[]', busy: false,
    onInboundTagChange: noop, onUsersJsonChange: noop, onManageInbound: noop, onSyncUsers: noop,
  })
  assert.match(html, /Управление не проверено/)
  assert.match(html, /Режим управления ещё не подтверждён/)
  assert.doesNotMatch(html, /Отключить синхронизацию/)
  assert.doesNotMatch(html, /Включить синхронизацию/)
  assert.match(html, /<button[^>]*disabled=""[^>]*>Отправить список клиентов<\/button>/)
})

test('Xray maintenance terms, impact warnings and safe statuses exist in RU/EN/FA/ZH', async () => {
  const ru = JSON.parse(readFileSync('public/statics/locales/ru.json', 'utf8')).nodes.xrayClients
  const expectedKeys = Object.keys(ru).sort()
  assert.ok(expectedKeys.length >= 20)
  for (const language of ['en', 'fa', 'zh']) {
    const current = JSON.parse(readFileSync('public/statics/locales/' + language + '.json', 'utf8')).nodes.xrayClients
    assert.deepEqual(Object.keys(current).sort(), expectedKeys, language + ' must cover all client-management labels')
    for (const key of expectedKeys) assert.ok(current[key].trim(), language + ':' + key + ' is empty')
    assert.ok(current.manualStatus.includes('{{count}}'), language + ': manual count substitution missing')
    assert.ok(current.manualConfirmDescription, language + ': manual replacement warning is missing')
    const rendered = await renderClients({
      inbounds: [], managedInboundTags: [], runtime: undefined,
      inboundTag: '', usersJson: '', busy: false,
      onInboundTagChange: noop, onUsersJsonChange: noop, onManageInbound: noop, onSyncUsers: noop,
    }, language)
    assert.ok(rendered.includes(current.sectionTitle), language + ' uses localized title')
  }
})

test('Node detail owns its only title and places read-only inbounds ahead of dangerous Xray operations', () => {
  const layout = readFileSync('src/pages/_dashboard.nodes.tsx', 'utf8')
  assert.match(layout, /!isCoreEditorPage && !isNodeDetailPage && \(/, 'Outer page header must not duplicate Node detail title')
  const detail = readFileSync('src/features/nodes/components/node-registry-panel.tsx', 'utf8')
  assert.match(detail, /<h1[^>]*>\{currentNode\?\.display_name/)
  assert.doesNotMatch(detail, /<CardTitle>|<CardDescription>/)
  assert.ok(detail.indexOf('<NodeXrayClientManagement') < detail.indexOf('<NodeXrayRuntimeControls'), 'Read-only inbounds precede manual runtime actions')
  assert.doesNotMatch(detail, /window\.confirm\(warning\)/, 'Maintenance uses the scoped accessible dialog, not window.confirm')
})
