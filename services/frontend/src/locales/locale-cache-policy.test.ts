import { expect, test } from 'bun:test'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'

const frontendRoot = fileURLToPath(new URL('../..', import.meta.url))
const read = (path: string) => readFileSync(new URL(path, `file://${frontendRoot}/`), 'utf8')

test('i18next bypasses obsolete cached translation JSON on new builds', () => {
  const source = read('src/locales/i18n.ts')
  expect(source).toContain("cache: 'no-store'")
  expect(source).toContain('statics/locales/{{lng}}.json')
})

test('both frontend Nginx topologies exempt locale JSON from immutable caching', () => {
  for (const name of ['nginx.conf', 'nginx.bundled.conf']) {
    const nginx = read(`deploy/${name}`)
    const localeLocation = nginx.indexOf('location /statics/locales/ {')
    const immutableLocation = nginx.indexOf('location /statics/ {')
    expect(localeLocation).toBeGreaterThan(0)
    expect(localeLocation).toBeLessThan(immutableLocation)
    expect(nginx.slice(localeLocation, immutableLocation)).toContain('Cache-Control "no-store"')
    expect(nginx.slice(immutableLocation)).toContain('Cache-Control "public, max-age=31536000, immutable"')
  }
})
