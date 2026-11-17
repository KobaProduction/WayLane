import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { FIREWALL_SCENARIO_KEYS } from './firewall-workspace-contract'

const langs = ['ru', 'en', 'fa', 'zh'] as const
const load = (code: string) => JSON.parse(readFileSync(`public/statics/locales/${code}.json`, 'utf8'))

function allLeaves(tree: unknown, prefix = ''): string[] {
  if (typeof tree === 'string') return [prefix]
  if (tree === null || typeof tree !== 'object' || Array.isArray(tree)) return []
  return Object.entries(tree).flatMap(([key, value]) => allLeaves(value, prefix ? `${prefix}.${key}` : key))
}

test('Subscription Firewall UI has matching, nonempty translations for each supported language', () => {
  const expected = allLeaves(load('ru').subscriptionFirewall.ui).sort()
  assert.ok(expected.length >= 130, `Expected complete Firewall UI coverage, got ${expected.length}`)
  for (const code of langs) {
    const data = load(code)
    const copy = data.subscriptionFirewall.ui
    assert.deepEqual(allLeaves(copy).sort(), expected, `${code} has missing or conflicting keys`)
    for (const key of expected) {
      const text = key.split('.').reduce<unknown>((value, part) => (value as Record<string, unknown>)[part], copy)
      assert.equal(typeof text, 'string', `${code}:${key} should be text`)
      assert.ok((text as string).trim(), `${code}:${key} is blank`)
    }
    for (const scenario of FIREWALL_SCENARIO_KEYS) {
      assert.equal(typeof copy.scenario[scenario], 'string', `${code}:scenario.${scenario} missing`)
    }
    for (const state of ['pending', 'sending', 'failed', 'delivered', 'exhausted']) {
      assert.equal(typeof copy.delivery[state], 'string', `${code}:delivery.${state} missing`)
      assert.equal(typeof copy.eventState[state], 'string', `${code}:eventState.${state} missing`)
    }
  }
})

test('all explicit Firewall UI translation keys used in the source exist in the bundles', () => {
  const code = [
    'src/features/subscriptions/components/subscription-firewall-designer.tsx',
    'src/features/subscriptions/components/subscription-firewall-events.tsx',
    'src/pages/_dashboard.subscription-firewall.tsx',
  ].map(name => readFileSync(name, 'utf8')).join('\n')
  const keys = [...code.matchAll(/subscriptionFirewall\.ui\.([A-Za-z0-9_.]+)/g)].map(match => match[1])
  const copy = load('en').subscriptionFirewall.ui
  for (const key of keys) {
    // Template literals append a scenario, status, or event kind at runtime.
    if (key.endsWith('.')) continue
    const value = key.split('.').reduce<unknown>((node, part) => (node as Record<string, unknown> | undefined)?.[part], copy)
    assert.equal(typeof value, 'string', `missing localized UI key: ${key}`)
  }
})
