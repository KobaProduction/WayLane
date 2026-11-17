import { test } from 'node:test'
import assert from 'node:assert/strict'
import { registrationCounts, filterByRegistration } from './node-registry-overview'

test('registration overview counts authorizations without inventing connectivity', () => {
  const nodes = [
    { status: 'approved' as const, name: 'approved-but-not-connected' },
    { status: 'pending' as const, name: 'awaiting-approval' },
    { status: 'revoked' as const, name: 'revoked-access' },
    { status: 'approved' as const, name: 'another-approved' },
  ]
  assert.deepEqual(registrationCounts(nodes), { all: 4, approved: 2, pending: 1, revoked: 1 })
  assert.deepEqual(filterByRegistration(nodes, 'approved').map(n => n.name), ['approved-but-not-connected', 'another-approved'])
  assert.deepEqual(filterByRegistration(nodes, 'pending').map(n => n.name), ['awaiting-approval'])
  assert.deepEqual(filterByRegistration(nodes, 'revoked').map(n => n.name), ['revoked-access'])
  assert.deepEqual(filterByRegistration(nodes, 'all'), nodes)
  assert.deepEqual(registrationCounts([]), { all: 0, approved: 0, pending: 0, revoked: 0 })
})
