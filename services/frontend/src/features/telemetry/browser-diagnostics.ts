/**
 * Browser -> authenticated WayLane backend -> OTLP.
 *
 * Never transmit error.message, stack traces, href, hash, credentials,
 * arbitrary URLs, text from the page or a full user identifier.
 */
import { $fetch } from '@/service/http'
import { getAuthToken } from '@/utils/authStorage'

type Kind = 'uncaught_error' | 'unhandled_rejection' | 'chunk_load_error'
type Group = 'login' | 'dashboard' | 'settings' | 'other'

const LIMIT = 10
const WINDOW_MS = 60_000
let windowStart = 0
let sent = 0
let installed = false

function group(): Group {
  // Use coarse static route groups only. Browser URL/hash may contain tokens.
  const fragment = window.location.hash
  if (fragment.startsWith('#/login')) return 'login'
  if (fragment.startsWith('#/settings')) return 'settings'
  if (fragment.startsWith('#/')) return 'dashboard'
  return 'other'
}

function send(kind: Kind): void {
  // The existing auth client is responsible for providing an authenticated
  // admin token. Anonymous authentication errors do not generate telemetry.
  if (!getAuthToken()) return
  const now = Date.now()
  if (now - windowStart > WINDOW_MS) {
    windowStart = now
    sent = 0
  }
  if (++sent > LIMIT) return
  void $fetch('/api/telemetry/browser', {
    method: 'POST',
    body: { kind, route_group: group() },
    retry: 0,
    timeout: 3000,
  }).catch(() => {
    // Telemetry is best effort. Never recursively report export failure.
  })
}

export function installBrowserDiagnostics(): void {
  if (installed) return
  installed = true
  window.addEventListener('error', () => send('uncaught_error'))
  window.addEventListener('unhandledrejection', () => send('unhandled_rejection'))
  // Any retryable UI chunk loader can reuse this safe indicator; not a URL.
}

export function reportChunkLoadFailure(): void {
  send('chunk_load_error')
}
