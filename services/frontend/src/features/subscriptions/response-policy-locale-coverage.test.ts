import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { POLICY_LANGUAGES, POLICY_REASONS } from './response-policy-contract'

const locales = ['ru', 'en', 'fa', 'zh'] as const
const copy = (locale: string) => JSON.parse(readFileSync(`public/statics/locales/${locale}.json`, 'utf8'))
  .subscriptionResponsePolicy.ui as Record<string, unknown>

function paths(value: unknown, prefix = ''): string[] {
  if (typeof value === 'string') return [prefix]
  if (!value || typeof value !== 'object' || Array.isArray(value)) return []
  return Object.entries(value).flatMap(([key, child]) => paths(child, prefix ? `${prefix}.${key}` : key))
}

function get(root: Record<string, unknown>, key: string): unknown {
  return key.split('.').reduce<unknown>((value, part) => (value as Record<string, unknown> | undefined)?.[part], root)
}

test('Subscription Response Policy has matching non-empty labels and validation in four languages', () => {
  const expected = paths(copy('ru')).sort()
  assert(expected.length >= 120, `Incomplete policy labels: ${expected.length}`)
  for (const locale of locales) {
    const translations = copy(locale)
    assert.deepEqual(paths(translations).sort(), expected, `Keys missing or misplaced: ${locale}`)
    for (const key of expected) {
      const text = get(translations, key)
      assert.equal(typeof text, 'string', `${locale}:${key} is not text`)
      assert((text as string).trim().length > 0, `${locale}:${key} is empty`)
    }
    for (const reason of POLICY_REASONS) assert.equal(typeof get(translations, `reason.${reason}`), 'string')
    for (const format of ['links', 'links_base64', 'clash', 'xray', 'sing_box']) {
      assert.equal(typeof get(translations, `format.${format}`), 'string')
    }
    for (const language of POLICY_LANGUAGES) assert(locales.includes(language))
  }
})

test('all static policy UI translation references are in the language bundles', () => {
  const source = readFileSync('src/pages/_dashboard.subscription-response-policies.tsx', 'utf8')
  const expected = [...source.matchAll(/tc\('([A-Za-z0-9.]+)'\)/g)].map(match => match[1])
  const fixed = [...source.matchAll(/subscriptionResponsePolicy\.ui\.([A-Za-z0-9.]+)/g)].map(match => match[1])
  for (const locale of locales) {
    const translations = copy(locale)
    for (const key of [...expected, ...fixed]) {
      // Dynamic i18next template literals are validated by known reason/format lists.
      if (key.endsWith('.')) continue
      assert.equal(typeof get(translations, key), 'string', `${locale}:${key} missing`)
    }
  }
})
