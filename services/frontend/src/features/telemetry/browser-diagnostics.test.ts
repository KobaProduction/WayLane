import assert from 'node:assert/strict'
import { test } from 'node:test'

import {
  classifyRouteHash,
  createBrowserDiagnosticReporter,
} from './browser-diagnostics'

test('only coarse route names are sent, never the hash or token', () => {
  assert.equal(classifyRouteHash('#/login?secret=private'), 'login')
  assert.equal(classifyRouteHash('#/settings/key/private'), 'settings')
  assert.equal(classifyRouteHash('#/nodes/sub/private'), 'dashboard')
  assert.equal(classifyRouteHash(''), 'other')
  assert.equal(classifyRouteHash('#not-a-route/private'), 'other')
})

test('an unauthenticated browser cannot submit diagnostics', () => {
  const sent: unknown[] = []
  const send = createBrowserDiagnosticReporter(
    () => false,
    () => 'login',
    async event => { sent.push(event) },
    () => 100_000,
  )
  send('uncaught_error')
  assert.deepEqual(sent, [])
})

test('reported events contain only allowlisted kinds and route groups', () => {
  const sent: unknown[] = []
  const send = createBrowserDiagnosticReporter(
    () => true,
    () => 'dashboard',
    async event => { sent.push(event) },
    () => 100_000,
  )
  send('chunk_load_error')
  send('render_error')
  assert.deepEqual(sent, [
    { kind: 'chunk_load_error', route_group: 'dashboard' },
    { kind: 'render_error', route_group: 'dashboard' },
  ])
})

test('ten events per minute are allowed and the counter resets', () => {
  const sent: unknown[] = []
  let now = 100_000
  const send = createBrowserDiagnosticReporter(
    () => true,
    () => 'settings',
    async event => { sent.push(event) },
    () => now,
  )
  for (let i = 0; i < 12; i++) send('uncaught_error')
  assert.equal(sent.length, 10)
  now += 60_000
  send('unhandled_rejection')
  assert.equal(sent.length, 11)
})

test('storage and transport failures do not propagate or re-report', async () => {
  let sends = 0
  const storageError = createBrowserDiagnosticReporter(
    () => { throw Error('unavailable') },
    () => 'login',
    async () => { sends += 1 },
  )
  assert.doesNotThrow(() => storageError('uncaught_error'))

  const transportError = createBrowserDiagnosticReporter(
    () => true,
    () => 'dashboard',
    async () => {
      sends += 1
      throw Error('offline')
    },
  )
  assert.doesNotThrow(() => transportError('render_error'))
  await Promise.resolve()
  assert.equal(sends, 1)
})
