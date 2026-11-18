import { Button } from '@/components/ui/button'
import { AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle } from '@/components/ui/alert-dialog'
import { confirmedRealityBinding, effectiveRealityPresetSelection, type RealityBinding } from '@/features/core-editor/kit/reality-preset-binding'
import { selectCoreEditorHasActualChanges } from '@/features/core-editor/kit/core-editor-change-state'
import { useCoreEditorStore } from '@/features/core-editor/state/core-editor-store'
import { Input } from '@/components/ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { $fetch } from '@/service/http'
import { getGetCoreConfigQueryKey, getListCoreBindingsQueryKey } from '@/service/api'
import { queryClient } from '@/utils/query-client'
import { useAdmin } from '@/hooks/use-admin'
import { hasPermission } from '@/utils/rbac'
import { generateRealityKeyPair } from '@/lib/xray-generation'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link } from 'react-router'
import { toast } from 'sonner'
import { useTranslation } from 'react-i18next'

export type RealityKeyMode = 'manual' | 'preset'

type RealityPreset = {
  id: string
  name: string
  kind: string
  revision: number
  data: { publicKey?: string; shortIds?: string[] }
}

interface Props {
  open: boolean
  coreId: number | null
  inboundSaved: boolean
  inboundTag: string
  initialPresetId?: string
  hasUnsavedChanges: boolean
  mode: RealityKeyMode
  onModeChange: (mode: RealityKeyMode) => void
  onApplied: () => void
}

function readableError(error: unknown, translate: (key: string) => string): string {
  const response = error as {
    status?: number
    data?: { detail?: unknown }
    response?: { status?: number; data?: { detail?: unknown } }
  }
  const detail = response?.data?.detail ?? response?.response?.data?.detail
  const status = response?.status ?? response?.response?.status
  if (typeof detail === 'string' && detail.includes('Core is assigned to an autonomous Node')) {
    return translate('assignedError')
  }
  if (status === 409) return translate('conflictError')
  if (status === 422) return translate('invalidKeyError')
  if (status === 403) return translate('permissionError')
  // Don't expose backend exception details to a subscription administrator.
  return translate('genericError')
}

export function RealityPresetModeSelector({ open, coreId, inboundSaved, inboundTag, initialPresetId, hasUnsavedChanges, mode, onModeChange, onApplied }: Props) {
  const { t } = useTranslation()
  const tr = (key: string, options?: Record<string, string | number>) => t(`coreEditor.realityPresetMode.${key}`, options)
  const { admin } = useAdmin()
  const canUpdate = hasPermission(admin, 'cores', 'update')
  const [presets, setPresets] = useState<RealityPreset[]>([])
  const [binding, setBinding] = useState<string | null>(null)
  const [selected, setSelected] = useState('')
  const [replacementKey, setReplacementKey] = useState('')
  const [pending, setPending] = useState(false)
  const [confirmation, setConfirmation] = useState<'manual' | 'preset' | null>(null)
  const [loading, setLoading] = useState(false)
  const [loadFailed, setLoadFailed] = useState(false)
  const [loadedScope, setLoadedScope] = useState<string | null>(null)
  const requestCounter = useRef(0)
  const scope = `${coreId ?? 'new'}:${inboundTag}`

  const refresh = useCallback(async () => {
    if (!open) return
    const currentRequest = ++requestCounter.current
    setLoadedScope(null)
    setLoading(true)
    setLoadFailed(false)
    try {
      const [available, assigned] = await Promise.all([
        $fetch<RealityPreset[]>('/api/core-presets'),
        $fetch<RealityBinding[]>('/api/core-presets/bindings'),
      ])
      if (currentRequest !== requestCounter.current) return
      const current = confirmedRealityBinding(assigned, coreId, inboundTag)
      setPresets(available.filter(item => item.kind === 'reality'))
      setBinding(current)
      setSelected(current ?? '')
      setLoadedScope(`${coreId ?? 'new'}:${inboundTag}`)
    } catch {
      if (currentRequest !== requestCounter.current) return
      setLoadFailed(true)
      setBinding(null)
      setPresets([])
    } finally {
      if (currentRequest === requestCounter.current) setLoading(false)
    }
  }, [coreId, inboundTag, open])

  useEffect(() => {
    if (open) void refresh()
    else {
      requestCounter.current += 1
      setLoadedScope(null)
    }
  }, [open, refresh])
  useEffect(() => { setReplacementKey(''); setConfirmation(null) }, [open, coreId, inboundTag])

  const currentPreset = useMemo(() => presets.find(item => item.id === binding), [presets, binding])
  // The list may be populated after the select mounts; keep the confirmed binding selected by default.
  const selectedValue = effectiveRealityPresetSelection(selected, binding)
  const selectedPreset = useMemo(() => presets.find(item => item.id === selectedValue), [presets, selectedValue])
  const verified = loadedScope === scope && !loading && !loadFailed
  const ready = verified && canUpdate && coreId !== null && inboundSaved && !pending
  const mismatch = verified && (initialPresetId ?? null) !== binding

  const requestConfirmation = (action: 'manual' | 'preset') => {
    if (hasUnsavedChanges || selectCoreEditorHasActualChanges(useCoreEditorStore.getState())) {
      toast.error(tr('unsaved'))
      return
    }
    setConfirmation(action)
  }

  const changePreset = async () => {
    if (!ready || !selectedValue || !coreId || mismatch) return
    if (hasUnsavedChanges || selectCoreEditorHasActualChanges(useCoreEditorStore.getState())) {
      toast.error(tr('unsavedPreset'))
      return
    }
    setPending(true)
    try {
      await $fetch('/api/core-presets/bindings', {
        method: 'POST', body: { core_id: coreId, inbound_tag: inboundTag, preset_id: selectedValue },
      })
      await queryClient.invalidateQueries({ queryKey: getListCoreBindingsQueryKey() })
      await queryClient.invalidateQueries({ queryKey: getGetCoreConfigQueryKey(coreId) })
      toast.success(tr('boundSuccess'))
      onApplied()
    } catch (error) {
      toast.error(readableError(error, tr))
    } finally { setPending(false) }
  }

  const switchToManual = async () => {
    if (!ready || !coreId || !binding || !replacementKey.trim() || mismatch) return
    if (hasUnsavedChanges || selectCoreEditorHasActualChanges(useCoreEditorStore.getState())) {
      toast.error(tr('unsavedKey'))
      return
    }
    setPending(true)
    try {
      await $fetch('/api/core-presets/bindings/reality/manual', {
        method: 'POST', body: { core_id: coreId, inbound_tag: inboundTag, private_key: replacementKey.trim() },
      })
      setReplacementKey('')
      await queryClient.invalidateQueries({ queryKey: getListCoreBindingsQueryKey() })
      await queryClient.invalidateQueries({ queryKey: getGetCoreConfigQueryKey(coreId) })
      toast.success(tr('manualSuccess'))
      onApplied()
    } catch (error) {
      toast.error(readableError(error, tr))
    } finally { setPending(false) }
  }

  return (
    <div className="space-y-3 rounded-md border bg-muted/20 p-3 sm:col-span-2" data-testid="reality-preset-mode-selector">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-sm font-medium">{tr('title')}</p>
        <details className="text-xs text-muted-foreground">
          <summary className="cursor-pointer">{tr('about')}</summary>
          <p className="mt-1 max-w-lg">{tr('aboutDescription')}</p>
        </details>
      </div>
      <Tabs value={mode} onValueChange={value => onModeChange(value === 'preset' ? 'preset' : 'manual')}>
        <TabsList className="grid w-full grid-cols-2">
          <TabsTrigger value="manual">{tr('manualTab')}</TabsTrigger>
          <TabsTrigger value="preset">{tr('presetTab')}</TabsTrigger>
        </TabsList>
      </Tabs>
      {(!verified && !loadFailed) && <p className="text-xs text-muted-foreground">{tr('loading')}</p>}
      {loadFailed && <div className="space-y-2"><p className="text-xs text-destructive">{tr('loadFailed')}</p><Button type="button" variant="outline" size="sm" onClick={() => void refresh()}>{tr('retry')}</Button></div>}
      {mismatch && <p className="text-xs text-destructive">{tr('mismatch')}</p>}
      {mode === 'preset' && (coreId === null || !inboundSaved) && <p className="text-xs text-muted-foreground">{tr('saveFirst')}</p>}
      {mode === 'preset' && !canUpdate && <p className="text-xs text-muted-foreground">{tr('noPermission')}</p>}
      {mode === 'manual' && binding && verified && (
        <div className="space-y-2">
          <p className="text-xs text-muted-foreground">{tr('boundInfo', { name: currentPreset?.name ?? binding })}</p>
          <details className="text-xs text-muted-foreground"><summary className="cursor-pointer">{tr('changeInfoTitle')}</summary>
            <p className="mt-1">{tr('changeInfo')}</p>
          </details>
          <Input type="password" autoComplete="new-password" aria-label={tr('manualKeyLabel')} placeholder={tr('manualKeyPlaceholder')} value={replacementKey} onChange={event => setReplacementKey(event.target.value)} />
          <div className="flex flex-wrap gap-2">
            <Button type="button" variant="outline" size="sm" onClick={() => setReplacementKey(generateRealityKeyPair().privateKey)}>{tr('generateKey')}</Button>
            <Button type="button" size="sm" disabled={!ready || !replacementKey.trim() || mismatch} onClick={() => requestConfirmation('manual')}>{tr('toManual')}</Button>
          </div>
        </div>
      )}
      {mode === 'preset' && (
        <div className="space-y-2">
          <Select value={selectedValue} onValueChange={setSelected} disabled={!verified}>
            <SelectTrigger aria-label={tr('presetSelect')}><SelectValue placeholder={tr('presetPlaceholder')} /></SelectTrigger>
            <SelectContent>{presets.map(item => <SelectItem key={item.id} value={item.id}>{item.name} · v{item.revision}</SelectItem>)}</SelectContent>
          </Select>
          {selectedPreset && (
            <details className="text-xs text-muted-foreground">
              <summary className="cursor-pointer">{tr('publicSummary', { count: (selectedPreset.data.shortIds ?? []).length })}</summary>
              <div className="mt-2 space-y-1">
                <p>{tr('publicKey')}: <code className="break-all">{selectedPreset.data.publicKey ?? '—'}</code></p>
                <p>{tr('shortIds')}: {(selectedPreset.data.shortIds ?? []).join(', ') || '—'}</p>
                <p>{tr('secretNote')}</p>
              </div>
            </details>
          )}
          {presets.length === 0 && verified && <p className="text-xs text-muted-foreground">{tr('empty')}</p>}
          <div className="flex flex-wrap items-center gap-2">
            {selectedValue === binding && binding && verified
              ? <span className="text-xs text-muted-foreground">{tr('alreadyBound')}</span>
              : selectedValue && <Button type="button" disabled={!ready || mismatch} size="sm" onClick={() => requestConfirmation('preset')}>{binding ? tr('replacePreset') : tr('bindPreset')}</Button>}
            <Button type="button" size="sm" variant="outline" onClick={() => void refresh()}>{tr('refresh')}</Button>
            <Link className="text-xs text-primary underline" to="/nodes/presets?kind=reality" target="_blank" rel="noopener noreferrer">{tr('manage')}</Link>
          </div>

        </div>
      )}
      <AlertDialog open={confirmation !== null} onOpenChange={value => { if (!value) setConfirmation(null) }}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{confirmation === 'manual' ? tr('manualConfirmTitle') : tr('presetConfirmTitle')}</AlertDialogTitle>
            <AlertDialogDescription>
              {confirmation === 'manual'
                ? tr('manualConfirmText')
                : tr('presetConfirmText', { core: coreId ?? '—', inbound: inboundTag })}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>{tr('cancel')}</AlertDialogCancel>
            <AlertDialogAction disabled={!ready || mismatch} onClick={() => {
              const action = confirmation
              setConfirmation(null)
              if (action === 'manual') void switchToManual()
              if (action === 'preset') void changePreset()
            }}>{tr('confirm')}</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}
