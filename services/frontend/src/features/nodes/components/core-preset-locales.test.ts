import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
const locales = ['ru', 'en', 'fa', 'zh']
const translations = (lang: string) => JSON.parse(readFileSync(`public/statics/locales/${lang}.json`, 'utf8')).nodes.presets.workspace as Record<string, string>

test('Xray Presets workspaces have consistent nonempty UI copy in four languages', () => {
  const base = Object.keys(translations('ru')).sort()
  assert.ok(base.length >= 90)
  for (const lang of locales) {
    const copy = translations(lang)
    assert.deepEqual(Object.keys(copy).sort(), base, `Missing preset labels for ${lang}`)
    for (const [key, text] of Object.entries(copy)) assert.ok(text.trim(), `${lang}:${key} blank`)
  }
})

test('every Xray preset UI translation reference has a key', () => {
  const code = readFileSync('src/features/nodes/components/core-presets-panel.tsx', 'utf8')
  const copy = translations('en')
  const keys = [...code.matchAll(/tx\('([A-Za-z0-9]+)'\)/g)].map(match => match[1])
  for (const key of keys) assert.equal(typeof copy[key], 'string', `Missing translation: ${key}`)
  const dynamic = [...code.matchAll(/nodes\.presets\.workspace\.([A-Za-z0-9]+)/g)].map(match => match[1])
  for (const key of dynamic) assert.equal(typeof copy[key], 'string', `Missing dynamic translation: ${key}`)
})
