import type { Profile, RoutingBalancer, RoutingRule } from '@waylane/xray-config-kit'

/** A local inspection of configured tags. No outbound probes or live routing decisions occur here. */
export type BalancerTopologyRow = {
  tag: string
  selectors: string[]
  matchingOutboundTags: string[]
  unmatchedSelectors: string[]
  fallbackTag: string | null
  fallbackMissing: boolean
  referencingRules: number[]
}

export type RoutingTargetRow = {
  index: number
  outboundTag: string | null
  balancerTag: string | null
  target: 'outbound' | 'balancer' | 'none'
  targetMissing: boolean
  balancerIgnored: boolean
}

export type RoutingTopology = {
  outboundTags: string[]
  balancers: BalancerTopologyRow[]
  rules: RoutingTargetRow[]
}

function names(values: readonly (string | null | undefined)[]): string[] {
  return [...new Set(values.map(value => typeof value === 'string' ? value.trim() : '').filter(Boolean))]
}

export function inspectRoutingTopology(profile: Profile): RoutingTopology {
  const outboundTags = names((profile.outbounds ?? []).map(outbound => outbound.tag))
  const balancers: RoutingBalancer[] = profile.routing?.balancers ?? []
  const rules: RoutingRule[] = profile.routing?.rules ?? []
  const balancerTags = names(balancers.map(item => item.tag))

  return {
    outboundTags,
    balancers: balancers.map(balancer => {
      const selectors = names(balancer.selector ?? [])
      const matchingOutboundTags = outboundTags.filter(tag => selectors.some(selector => tag.startsWith(selector)))
      const unmatchedSelectors = selectors.filter(selector => !outboundTags.some(tag => tag.startsWith(selector)))
      const fallbackTag = balancer.fallbackTag?.trim() || null
      return {
        tag: balancer.tag,
        selectors,
        matchingOutboundTags,
        unmatchedSelectors,
        fallbackTag,
        fallbackMissing: fallbackTag !== null && !outboundTags.includes(fallbackTag),
        referencingRules: rules.flatMap((rule, index) => rule.balancerTag === balancer.tag && !rule.outboundTag ? [index] : []),
      }
    }),
    rules: rules.map((rule, index) => {
      const outboundTag = rule.outboundTag?.trim() || null
      const balancerTag = rule.balancerTag?.trim() || null
      return {
        index,
        outboundTag,
        balancerTag,
        target: outboundTag ? 'outbound' as const : balancerTag ? 'balancer' as const : 'none' as const,
        targetMissing: outboundTag !== null ? !outboundTags.includes(outboundTag) : balancerTag !== null ? !balancerTags.includes(balancerTag) : true,
        balancerIgnored: outboundTag !== null && balancerTag !== null,
      }
    }),
  }
}

/** Prevent dangling routing.balancerTag references; requires an explicit rule reassignment. */
export function referencedBalancerTags(profile: Profile, indices: readonly number[]): string[] {
  const balancers = profile.routing?.balancers ?? []
  const rules = profile.routing?.rules ?? []
  const tags = new Set(indices.flatMap(index => balancers[index]?.tag ? [balancers[index].tag] : []))
  return [...tags].filter(tag => rules.some(rule => rule.balancerTag === tag))
}

/** Renaming a balancer preserves links from all routing rules in the same Core. */
export function renameBalancerWithReferences(profile: Profile, index: number, replacement: RoutingBalancer): Profile {
  const routing = profile.routing
  if (!routing || !routing.balancers?.[index]) return profile
  const previousTag = routing.balancers[index].tag
  const balancers = [...routing.balancers]
  balancers[index] = replacement
  const rules = previousTag !== replacement.tag
    ? routing.rules.map(rule => rule.balancerTag === previousTag ? { ...rule, balancerTag: replacement.tag } : rule)
    : routing.rules
  return { ...profile, routing: { ...routing, balancers, rules } }
}

/** Never silently create probe settings when a user merely visits the Balancers tab. */
export function balancersMissingObservation(profile: Profile): boolean {
  const needsObservation = (profile.routing?.balancers ?? []).some(item =>
    item.strategy?.type === 'leastPing' || item.strategy?.type === 'leastLoad')
  if (!needsObservation) return false
  return profile.raw?.topLevel?.observatory === undefined && profile.raw?.topLevel?.burstObservatory === undefined
}
