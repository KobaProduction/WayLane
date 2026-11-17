export const TLS_WORKSPACE_TABS = ['catalog', 'acme', 'bindings', 'webhook'] as const
export type TlsWorkspaceTab = typeof TLS_WORKSPACE_TABS[number]

/** Invalid direct links return to the catalogue, without mutating a certificate. */
export function validTlsWorkspaceTab(value: string | null): TlsWorkspaceTab {
  return TLS_WORKSPACE_TABS.find(item => item === value) ?? 'catalog'
}

export type CertificateExpiry = 'valid' | 'soon' | 'expired' | 'unknown'

export function certificateExpiry(notAfter: string | null | undefined, at: number): CertificateExpiry {
  if (!notAfter) return 'unknown'
  const expiry = Date.parse(notAfter)
  if (!Number.isFinite(expiry)) return 'unknown'
  if (expiry <= at) return 'expired'
  return expiry - at <= 30 * 24 * 60 * 60 * 1000 ? 'soon' : 'valid'
}
