import type { ValidationListItem } from '@/features/core-editor/components/shared/validation-summary'
import { validateProfileForPersist, type ManagedCorePresetBinding } from '@/features/core-editor/kit/xray-adapter'
import { useListCoreBindings } from '@/service/api'
import { useCoreEditorStore } from '@/features/core-editor/state/core-editor-store'
import { useMemo } from 'react'

/** Only a matching server-owned binding may suppress validation of an intentionally absent managed secret. */
function verifiedBindings(data: unknown, coreId: number | null): ManagedCorePresetBinding[] {
  if (coreId === null || !Array.isArray(data)) return []
  return data.flatMap((entry: unknown) => {
    if (typeof entry !== 'object' || entry === null || Array.isArray(entry)) return []
    const item = entry as Record<string, unknown>
    if (item.core_id !== coreId || typeof item.preset_id !== 'string' || typeof item.inbound_tag !== 'string' ||
        (item.kind !== 'reality' && item.kind !== 'vless' && item.kind !== 'routing')) return []
    return [{ kind: item.kind, preset_id: item.preset_id, inbound_tag: item.inbound_tag }]
  })
}

/** Same list as the Xray branch of `preSaveIssues` on the core editor page (strict blockers + core-kit issues). */
export function useXrayPersistValidationItems(): ValidationListItem[] {
  const hydrated = useCoreEditorStore(s => s.hydrated)
  const kind = useCoreEditorStore(s => s.kind)
  const profile = useCoreEditorStore(s => s.xrayProfile)
  const coreId = useCoreEditorStore(s => s.coreId)
  const { data: rawBindings, isPending: bindingsPending } = useListCoreBindings({
    query: { enabled: hydrated && kind === 'xray' && coreId !== null },
  })
  const bindings = useMemo(() => verifiedBindings(rawBindings, coreId), [rawBindings, coreId])

  return useMemo(() => {
    if (!hydrated || kind !== 'xray' || !profile || (coreId !== null && bindingsPending)) return []
    const r = validateProfileForPersist(profile, bindings)
    if (r.ok) return []
    const items: ValidationListItem[] = r.strictBlockers.map(issue => ({ source: 'xray' as const, issue }))
    for (const issue of r.coreKitIssues) {
      items.push({ source: 'core-kit' as const, issue })
    }
    return items
  }, [hydrated, kind, profile, coreId, bindingsPending, bindings])
}
