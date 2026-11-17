import assert from 'node:assert/strict'
import { test } from 'node:test'
import type { Profile } from '@waylane/xray-config-kit'
import { inspectRoutingTopology, referencedBalancerTags, renameBalancerWithReferences, balancersMissingObservation } from './balancer-routing-topology'

const profile = {
  outbounds: [{ protocol: 'freedom', tag: 'direct' }, { protocol: 'vless', tag: 'proxy-1' }, { protocol: 'vless', tag: 'proxy-2' }],
  routing: {
    domainStrategy: 'AsIs',
    balancers: [
      { tag: 'auto', selector: ['proxy-'], fallbackTag: 'direct', strategy: { type: 'leastPing' } },
      { tag: 'unused', selector: ['future-'], fallbackTag: 'gone' },
    ],
    rules: [
      { type: 'field', balancerTag: 'auto' },
      { type: 'field', outboundTag: 'direct', balancerTag: 'auto' },
      { type: 'field', balancerTag: 'removed' },
      { type: 'field', outboundTag: 'missing-outbound' },
    ],
  },
} as unknown as Profile

test('tag-prefix preview is read-only and resolves outbound and routing references', () => {
  const original = JSON.stringify(profile)
  const topology = inspectRoutingTopology(profile)
  assert.deepEqual(topology.balancers[0].matchingOutboundTags, ['proxy-1', 'proxy-2'])
  assert.deepEqual(topology.balancers[0].unmatchedSelectors, [])
  assert.deepEqual(topology.balancers[0].referencingRules, [0])
  assert.equal(topology.balancers[0].fallbackMissing, false)
  assert.deepEqual(topology.balancers[1].unmatchedSelectors, ['future-'])
  assert.equal(topology.balancers[1].fallbackMissing, true)
  assert.equal(topology.rules[0].target, 'balancer')
  assert.equal(topology.rules[1].target, 'outbound')
  assert.equal(topology.rules[1].balancerIgnored, true)
  assert.equal(topology.rules[2].targetMissing, true)
  assert.equal(topology.rules[3].targetMissing, true)
  assert.equal(JSON.stringify(profile), original, 'preview must not mark a clean profile dirty')
})

test('referenced balancers are protected from single and bulk removal', () => {
  assert.deepEqual(referencedBalancerTags(profile, [0]), ['auto'])
  assert.deepEqual(referencedBalancerTags(profile, [1]), [])
  assert.deepEqual(referencedBalancerTags(profile, [0, 1]), ['auto'])
  assert.deepEqual(referencedBalancerTags(profile, []), [])
})


test('renaming a Balancer updates inbound routing references without changing other targets', () => {
  const before = JSON.stringify(profile)
  const changed = renameBalancerWithReferences(profile, 0, {
    tag: 'new-auto', selector: ['proxy-'], fallbackTag: 'direct', strategy: { type: 'leastPing' },
  })
  assert.equal(changed.routing?.balancers?.[0].tag, 'new-auto')
  assert.equal(changed.routing?.rules?.[0].balancerTag, 'new-auto')
  assert.equal(changed.routing?.rules?.[1].balancerTag, 'new-auto')
  assert.equal(changed.routing?.rules?.[2].balancerTag, 'removed')
  assert.equal(changed.routing?.rules?.[1].outboundTag, 'direct')
  assert.equal(JSON.stringify(profile), before, 'rename must not mutate source profile')
  assert.equal(renameBalancerWithReferences(profile, 99, { tag: 'none', selector: [] }), profile)
})


test('a missing probe source is reported, never auto-created on read', () => {
  const clean = JSON.stringify(profile)
  assert.equal(balancersMissingObservation(profile), true)
  assert.equal(JSON.stringify(profile), clean)
  const configured = { ...profile, raw: { topLevel: { observatory: { subjectSelector: ['proxy-'] } } } } as Profile
  assert.equal(balancersMissingObservation(configured), false)
  const roundRobin = { ...profile, routing: { ...profile.routing, balancers: [{ tag: 'round', selector: ['proxy-'], strategy: { type: 'roundRobin' } }] } } as Profile
  assert.equal(balancersMissingObservation(roundRobin), false)
})
