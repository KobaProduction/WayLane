import { expect, test } from 'bun:test'
import { OFFICIAL_NODE_REPOSITORY, approvedNodeReleaseImage, withNodeReleaseRepository } from './node-release-image'

const second = 'registry.example.com:5000/waylane/node'
const digest = 'a'.repeat(64)

test('clears digest when switching from GHCR to a separately configured OCI registry', () => {
  const ghcr = `${OFFICIAL_NODE_REPOSITORY}@sha256:${digest}`
  const custom = `${second}@sha256:${digest}`
  expect(withNodeReleaseRepository(second)).toBe(`${second}@sha256:`)
  expect(approvedNodeReleaseImage(ghcr, [OFFICIAL_NODE_REPOSITORY, second])).toBe(true)
  expect(approvedNodeReleaseImage(custom, [OFFICIAL_NODE_REPOSITORY, second])).toBe(true)
  expect(approvedNodeReleaseImage(custom, [OFFICIAL_NODE_REPOSITORY])).toBe(false)
})

test('rejects mutable, untrusted, truncated and uppercase references', () => {
  const candidates = [
    `${second}:latest`, `evil.example.com/waylane/node@sha256:${digest}`,
    `${second}@sha256:${'A'.repeat(64)}`, `${second}@sha256:${'a'.repeat(63)}`,
    `https://${second}@sha256:${digest}`,
  ]
  for (const candidate of candidates) {
    expect(approvedNodeReleaseImage(candidate, [OFFICIAL_NODE_REPOSITORY, second])).toBe(false)
  }
})
