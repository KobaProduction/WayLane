import { test } from 'node:test'
import assert from 'node:assert/strict'
import {
  FIREWALL_SCENARIO_KEYS, validFirewallScenario, validFirewallTab,
  validateMessageTemplate, validateFirewallDraft, validateWebhookFields,
  firewallDraftChanged, statusLabel, deviceEventLabel,
} from './firewall-workspace-contract'
import type { FirewallEditor } from './firewall-workspace-contract'

const template = { title: 'Сообщение', lines: ['Информационная строка'] }
const config: FirewallEditor = {
  enabled: true,
  webhook_enabled: false,
  webhook_url: null,
  device_check_url: null,
  scenarios: Object.fromEntries(FIREWALL_SCENARIO_KEYS.map(key => [key, template])) as FirewallEditor['scenarios'],
}

test('workspace deep links are whitelisted and cannot select arbitrary scenarios', () => {
  for (const tab of ['messages', 'preview', 'webhook', 'events']) assert.equal(validFirewallTab(tab), tab)
  for (const invalid of [null, '', 'another', 'MESSAGES', '../../events']) assert.equal(validFirewallTab(invalid), 'messages')
  for (const key of FIREWALL_SCENARIO_KEYS) assert.equal(validFirewallScenario(key), key)
  assert.equal(validFirewallScenario('other'), 'invalid_link')
})

test('message templates reject blank lines, excessive text and too many lines before preview/save', () => {
  assert.deepEqual(validateMessageTemplate(template), [])
  assert.ok(validateMessageTemplate({ title: ' ', lines: ['ok'] }).some(error => error.includes('Заголовок')))
  assert.ok(validateMessageTemplate({ title: 'ok', lines: [' '] }).some(error => error.includes('Каждая')))
  assert.ok(validateMessageTemplate({ title: 'ok', lines: Array.from({ length: 9 }, () => 'line') }).some(error => error.includes('8')))
  assert.ok(validateMessageTemplate({ title: 'ok', lines: ['x'.repeat(257)] }).some(error => error.includes('256')))
  assert.deepEqual(validateFirewallDraft(config), [])
  assert.ok(validateFirewallDraft({ ...config, scenarios: { ...config.scenarios, invalid_link: { title: '', lines: [] } } }).length >= 2)
})

test('webhook URL validation respects Backend responsibility for internal host allowlists', () => {
  assert.deepEqual(validateWebhookFields(config), [])
  assert.ok(validateWebhookFields({ ...config, webhook_enabled: true }).some(error => error.includes('требуется URL')))
  assert.deepEqual(validateWebhookFields({ ...config, webhook_enabled: true, webhook_url: 'https://example.com/device' }), [])
  assert.deepEqual(validateWebhookFields({ ...config, webhook_url: 'http://internal-receiver:8090/events' }), [])
  assert.ok(validateWebhookFields({ ...config, webhook_url: 'https://user:password@example.com/' }).length > 0)
  assert.ok(validateWebhookFields({ ...config, device_check_url: 'ftp://example.com' }).length > 0)
})

test('dirty tracking compares committed snapshot, not tabs or event list', () => {
  assert.equal(firewallDraftChanged(config, config), false)
  assert.equal(firewallDraftChanged(config, { ...config, enabled: false }), true)
  assert.equal(firewallDraftChanged(null, config), false)
  assert.equal(statusLabel('exhausted'), 'Попытки исчерпаны')
  assert.equal(statusLabel('invalid'), 'Неизвестное состояние')
})


test('device events use backend event type identifiers without inventing actions', () => {
  assert.equal(deviceEventLabel('device.added'), 'Устройство добавлено')
  assert.equal(deviceEventLabel('device.updated'), 'Устройство обновлено')
  assert.equal(deviceEventLabel('device.removed'), 'Устройство удалено')
  assert.equal(deviceEventLabel('device.reset'), 'Список устройств сброшен')
  assert.equal(deviceEventLabel('unknown.type'), 'unknown.type')
  assert.equal(statusLabel('failed'), 'Ошибка — ожидает повтора')
})
