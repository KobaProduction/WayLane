import { useQuery } from '@tanstack/react-query'

interface CachedRelease {
  version: string
  url: string
  timestamp: number
}

interface VersionCheckResult {
  hasUpdate: boolean
  latestVersion: string | null
  currentVersion: string | null
  releaseUrl: string | null
  isLoading: boolean
}

interface UseVersionCheckOptions {
  enabled?: boolean
}

const WAYLANE_RELEASE_API_URL = 'https://api.github.com/repos/KobaProduction/waylane/releases?per_page=100'
const CACHE_KEY = 'waylane_monorepo_backend_release'
const CACHE_DURATION = 10 * 60 * 1000

function compareVersions(current: string, latest: string): number {
  const currentParts = current.replace(/^v/, '').split('.').map(Number)
  const latestParts = latest.replace(/^v/, '').split('.').map(Number)

  for (let i = 0; i < Math.max(currentParts.length, latestParts.length); i++) {
    const curr = currentParts[i] || 0
    const lat = latestParts[i] || 0
    if (curr < lat) return -1
    if (curr > lat) return 1
  }
  return 0
}

function getCached(): CachedRelease | null {
  try {
    const cached = localStorage.getItem(CACHE_KEY)
    if (!cached) return null
    return JSON.parse(cached)
  } catch {
    return null
  }
}

function setCache(version: string, url: string): void {
  try {
    const data: CachedRelease = { version, url, timestamp: Date.now() }
    localStorage.setItem(CACHE_KEY, JSON.stringify(data))
  } catch {
    return
  }
}

async function fetchLatestRelease(): Promise<{ version: string; url: string } | null> {
  const cached = getCached()
  if (cached && Date.now() - cached.timestamp < CACHE_DURATION) {
    return { version: cached.version, url: cached.url }
  }

  try {
    const response = await fetch(WAYLANE_RELEASE_API_URL, {
      referrerPolicy: 'no-referrer',
      credentials: 'omit',
      headers: { Accept: 'application/json' },
    })

    if (!response.ok) {
      return cached ? { version: cached.version, url: cached.url } : null
    }

    // A monorepo release belongs to one component; never compare node
    // versions against backend or frontend releases.
    const releases = await response.json()
    if (!Array.isArray(releases)) return cached ? { version: cached.version, url: cached.url } : null
    const release = releases.find((item: { tag_name?: string; html_url?: string; draft?: boolean; prerelease?: boolean }) =>
      !item.draft && !item.prerelease && item.tag_name?.startsWith('backend-v'),
    )
    const version = release?.tag_name?.slice('backend-v'.length) || ''
    const url = release?.html_url || ''

    if (version) setCache(version, url)
    return { version, url }
  } catch {
    return cached ? { version: cached.version, url: cached.url } : null
  }
}

export function useVersionCheck(currentVersion: string | null, options: UseVersionCheckOptions = {}): VersionCheckResult {
  const enabled = options.enabled ?? true
  const { data, isLoading } = useQuery({
    queryKey: ['waylane-release-check'],
    queryFn: fetchLatestRelease,
    enabled,
    staleTime: CACHE_DURATION,
    gcTime: CACHE_DURATION * 2,
    refetchOnWindowFocus: false,
    refetchOnMount: false,
    refetchInterval: CACHE_DURATION,
    retry: 1,
  })

  const latestVersion = data?.version || null
  const cleanCurrentVersion = currentVersion?.replace(/^v/, '') || null

  const hasUpdate = enabled && !!(cleanCurrentVersion && latestVersion && compareVersions(cleanCurrentVersion, latestVersion) < 0)

  return {
    hasUpdate,
    latestVersion: enabled ? latestVersion : null,
    currentVersion: cleanCurrentVersion,
    releaseUrl: enabled ? data?.url || null : null,
    isLoading: enabled ? isLoading : false,
  }
}
