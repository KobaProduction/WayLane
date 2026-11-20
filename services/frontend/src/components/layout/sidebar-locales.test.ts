import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

test('TLS navigation item is localized for all supported admin languages', () => {
  const source = readFileSync('src/components/layout/sidebar.tsx', 'utf8')
  assert.match(source, /title: 'nodes\.tlsNav'/)
  assert.doesNotMatch(source, /title: 'TLS-сертификаты'/)
  for (const locale of ['ru', 'en', 'fa', 'zh']) {
    const copy = JSON.parse(readFileSync(`public/statics/locales/${locale}.json`, 'utf8'))
    assert.ok(copy.nodes.tlsNav.trim(), `${locale}: missing TLS label`)
  }
})
