import { test } from 'node:test'
import assert from 'node:assert/strict'
import { TLS_WORKSPACE_TABS, validTlsWorkspaceTab, certificateExpiry } from './tls-workspace-tabs'

test('TLS workspace direct links use only supported tabs', () => {
  for (const tab of TLS_WORKSPACE_TABS) assert.equal(validTlsWorkspaceTab(tab), tab)
  for (const invalid of [null, '', 'other', 'CATALOG', '../../admin']) assert.equal(validTlsWorkspaceTab(invalid), 'catalog')
})

test('certificate validity uses a deterministic 30-day threshold', () => {
  const at = Date.parse('2026-11-17T00:00:00Z')
  assert.equal(certificateExpiry('2026-11-16T23:59:59Z', at), 'expired')
  assert.equal(certificateExpiry('2026-11-17T00:00:00Z', at), 'expired')
  assert.equal(certificateExpiry('2026-12-01T00:00:00Z', at), 'soon')
  assert.equal(certificateExpiry('2026-12-17T00:00:00Z', at), 'soon')
  assert.equal(certificateExpiry('2026-12-18T00:00:00Z', at), 'valid')
  assert.equal(certificateExpiry(undefined, at), 'unknown')
  assert.equal(certificateExpiry('not-a-date', at), 'unknown')
})
