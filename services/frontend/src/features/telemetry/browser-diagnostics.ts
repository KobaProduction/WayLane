/**
 * Browser -> authenticated WayLane backend -> OTLP.
 *
 * Never transmit error.message, stack traces, href, hash, credentials,
 * arbitrary URLs, text from the page or a full user identifier.
 */
import { $fetch } from '@/service/http'
import { getAuthToken } from '@/utils/authStorage'

export type BrowserDiagnosticKind = 'uncaught_error' | 'unhandled_rejection' | 'chunk_load_error' | 'render_error'
export type BrowserRouteGroup = 'login' | 'dashboard' | 'settings' | 'other'
type BrowserDiagnostic = { kind: BrowserDiagnosticKind; route_group: BrowserRouteGroup }

const LIMIT = 10
const WINDOW_MS = 60_000
let installed = false

export function classifyRouteHash(fragment: string): BrowserRouteGroup {
  // Never send the actual hash: it may contain subscription or session tokens.
  if (fragment.startsWith('#/login')) return 'login'
  if (fragment.startsWith('#/settings')) return 'settings'
  if (fragment.startsWith('#/')) return 'dashboard'
  return 'other'
}

/** One bounded reporter per browser session, with no dynamic error payload. */
export function createBrowserDiagnosticReporter(
  isAuthenticated: () => boolean,
  routeGroup: () => BrowserRouteGroup,
  deliver: (body: BrowserDiagnostic) => Promise<unknown>,
  clock: () => number = Date.now,
): (kind: BrowserDiagnosticKind) => void {
  let windowStart = 0
  let sent = 0

  return kind => {
    // Anonymous client errors are not reported. This also avoids turning
    // an unauthenticated route into an attacker-controlled telemetry source.
    try {
      if (!isAuthenticated()) return
      const now = clock()
      if (now - windowStart >= WINDOW_MS) {
        windowStart = now
        sent = 0
      }
      if (sent >= LIMIT) return
      const event: BrowserDiagnostic = { kind, route_group: routeGroup() }
      sent += 1
      void deliver(event).catch(() => {
        // Best-effort telemetry; an export failure must not recurse.
      })
    } catch {
      // Restricted localStorage or a failed transport must not break the UI.
    }
  }
}

const send = createBrowserDiagnosticReporter(
  () => Boolean(getAuthToken()),
  () => classifyRouteHash(window.location.hash),
  body => $fetch('/api/telemetry/browser', {
    method: 'POST',
    body,
    retry: 0,
    timeout: 3000,
  }),
)

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

/** Report only a fixed event category, never React error objects or stacks. */
export function reportReactRenderFailure(): void {
  send('render_error')
}
