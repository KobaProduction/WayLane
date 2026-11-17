import assert from 'node:assert/strict'
import { test } from 'node:test'
import { routingActionTargetPatch } from './routing-action-target'

test('choosing a Balancer clears the outbound action so it actually receives traffic', () => {
  assert.deepEqual(routingActionTargetPatch('balancerTag', 'pool'), { balancerTag: 'pool', outboundTag: undefined })
  assert.deepEqual(routingActionTargetPatch('outboundTag', 'direct'), { outboundTag: 'direct', balancerTag: undefined })
  assert.deepEqual(routingActionTargetPatch('balancerTag', ''), { balancerTag: '' })
  assert.deepEqual(routingActionTargetPatch('domain', 'example.com'), { domain: 'example.com' })
})
