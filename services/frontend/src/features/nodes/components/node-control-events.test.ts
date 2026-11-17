import assert from 'node:assert/strict'
import { test } from 'node:test'
import { mostRecentConnectionEvent, nodeControlEventTitle, type NodeControlEvent } from './node-control-events'

const event = (kind: string, timestamp: string): NodeControlEvent => ({
  event_uuid: `${kind}-${timestamp}`, timestamp, kind, status: kind,
  core_id: null, generation: null,
})

test('connection history never conflates the latest Core event with a current WSS session', () => {
  const events = [
    event('xray_result', '2026-11-17T12:00:00Z'),
    event('disconnected', '2026-11-17T11:59:00Z'),
    event('connected', '2026-11-17T11:58:00Z'),
  ]
  assert.equal(mostRecentConnectionEvent(events)?.kind, 'disconnected')
  assert.equal(mostRecentConnectionEvent([event('xray_result', '2026-11-17T12:00:00Z')]), null)
  assert.equal(mostRecentConnectionEvent([]), null)
  assert.match(nodeControlEventTitle('connected'), /зарегистрировано/)
  assert.match(nodeControlEventTitle('disconnected'), /Отключение/)
  assert.equal(nodeControlEventTitle('unknown-kind'), 'Событие управления Node')
})
