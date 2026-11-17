import { test } from 'node:test'
import assert from 'node:assert/strict'
import { observedWssPresence } from './node-wss-presence'

const records = [
  { node_uuid: 'approved', wss_fresh: true, last_seen_at: '2026-11-17T08:00:00Z' },
  { node_uuid: 'stale', wss_fresh: false, last_seen_at: null },
]

test('WSS observations report only the saved read result, never a live socket state', () => {
  assert.equal(observedWssPresence('approved', 1000, records), 'recent')
  assert.equal(observedWssPresence('stale', 1000, records), 'stale')
  assert.equal(observedWssPresence('not-listed', 1000, records), 'unknown')
  assert.equal(observedWssPresence('approved', null, records), 'unknown')
  assert.equal(observedWssPresence('approved', 1000, null), 'unknown')
  // A previous observation remains historically true as-of its displayed check time.
  assert.equal(observedWssPresence('approved', 1000, records), 'recent')
})
