import { test } from 'node:test'
import assert from 'node:assert/strict'
import { getNotice, withNotice, responsePolicyErrors, previewIsExplicitlyInert, reasonLabel } from './response-policy-contract'
import type { ResponsePolicy } from './response-policy-contract'

const empty: ResponsePolicy = { enabled: false, rules: [], notices: {}, external_denial_codes: [] }

test('message templates are edited independently per reason and language', () => {
  const ru = withNotice(empty, 'device_limit', 'ru', { title: 'Лимит устройств', lines: ['Удалите старое'] })
  const en = withNotice(ru, 'device_limit', 'en', { title: 'Device limit', lines: ['Remove old device'] })
  assert.equal(getNotice(en, 'device_limit', 'ru').title, 'Лимит устройств')
  assert.equal(getNotice(en, 'device_limit', 'en').title, 'Device limit')
  assert.equal(getNotice(en, 'device_limit', 'fa').title, '')
  assert.deepEqual(empty.notices, {})
})

test('only a configured inert notice is represented by HTTP 200', () => {
  assert.equal(previewIsExplicitlyInert({ decision: 'notice_200', status: 200, wire_format: 'links_base64', language: 'ru', matched_rule_id: 'r', preview_title: 'x', preview_lines: ['x'], note: '' }), true)
  assert.equal(previewIsExplicitlyInert({ decision: 'normal_subscription', status: 200, wire_format: null, language: 'ru', matched_rule_id: null, preview_title: null, preview_lines: [], note: '' }), false)
  assert.equal(reasonLabel('external:PAYMENT_REQUIRED'), 'Внешний код: PAYMENT_REQUIRED')
})

test('missing notice and unsafe external codes fail client-side validation', () => {
  const enabled = { ...empty, enabled: true, rules: [{ id: 'a', name: 'Happ', reason: 'device_limit' as const, ua_match: 'contains' as const, ua_value: 'Happ', mode: 'notice_200' as const }] }
  assert.ok(responsePolicyErrors(enabled).some(error => error.includes('информационного')))
  assert.deepEqual(responsePolicyErrors({ ...enabled, notices: { device_limit: { ru: { title: 'Лимит', lines: ['Удалить устройство'] } } } }), [])
  assert.ok(responsePolicyErrors({ ...empty, external_denial_codes: ['BOT_ERROR'] }).some(error => error.includes('успешного допуска')))
})
