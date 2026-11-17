/** Route-level tabs, not a claim about current WSS connectivity or operational success. */
export const NODE_DETAIL_TABS = ['overview', 'operations', 'registry', 'xray', 'tls', 'diagnostics'] as const
export type NodeDetailTab = typeof NODE_DETAIL_TABS[number]

export function validNodeDetailTab(value: string | null): NodeDetailTab {
  return NODE_DETAIL_TABS.find(tab => tab === value) ?? 'overview'
}
