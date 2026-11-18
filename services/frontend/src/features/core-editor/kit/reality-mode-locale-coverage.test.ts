import assert from 'node:assert/strict'
import { test } from 'node:test'
import { readFileSync } from 'node:fs'

const languages = ['ru', 'en', 'fa', 'zh'] as const
const locales = (language: string): Record<string, string> => JSON.parse(
  readFileSync(`public/statics/locales/${language}.json`, 'utf8'),
).coreEditor.realityPresetMode

test('REALITY mode selector has complete, nonempty localized UI labels', () => {
  const expected = Object.keys(locales('ru')).sort()
  assert.ok(expected.length >= 40, 'All REALITY UI states, confirmation dialogs and errors need translations')
  for (const lang of languages) {
    const translations = locales(lang)
    assert.deepEqual(Object.keys(translations).sort(), expected, `${lang}: labels differ`)
    for (const key of expected) assert.ok(translations[key]?.trim(), `${lang}:${key} is blank`)
    for (const key of ['boundInfo', 'presetConfirmText']) {
      assert.ok(translations[key].includes('{{'), `${lang}:${key} must preserve substitutions`)
    }
  }
})

test('every static REALITY selector translation reference exists', () => {
  const source = readFileSync('src/features/core-editor/components/xray/reality-preset-mode-selector.tsx', 'utf8')
  const refs = [...source.matchAll(/tr\('([a-zA-Z0-9]+)'/g)].map(match => match[1])
  for (const language of languages) {
    const translations = locales(language)
    for (const key of refs) {
      assert.equal(typeof translations[key], 'string', `Missing ${language}:${key}`)
    }
  }
})
