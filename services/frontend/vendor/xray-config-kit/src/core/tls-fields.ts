/** Xray `tlsSettings.verifyPeerCertByName` is a comma-separated string. The kit may also see the legacy array form. */

export function coerceVerifyPeerCertByNameList(value: unknown): string[] | undefined {
  if (value == null) return undefined

  if (typeof value === "string") {
    const parts = value.split(",").map((item) => item.trim()).filter(Boolean)
    return parts.length > 0 ? parts : undefined
  }

  if (Array.isArray(value)) {
    const parts = value.map((item) => String(item ?? "").trim()).filter(Boolean)
    return parts.length > 0 ? parts : undefined
  }

  return undefined
}

export function stringifyVerifyPeerCertByName(value: unknown): string | undefined {
  const parts = coerceVerifyPeerCertByNameList(value)
  return parts && parts.length > 0 ? parts.join(",") : undefined
}
