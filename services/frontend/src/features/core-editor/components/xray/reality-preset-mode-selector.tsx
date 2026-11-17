import { Button } from '@/components/ui/button'
import { confirmedRealityBinding, effectiveRealityPresetSelection, type RealityBinding } from '@/features/core-editor/kit/reality-preset-binding'
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
import { toast } from 'sonner'

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

function readableError(error: unknown): string {
  const response = error as { data?: { detail?: unknown }; response?: { data?: { detail?: unknown } }; message?: string }
  const detail = response?.data?.detail ?? response?.response?.data?.detail
  return typeof detail === 'string' ? detail : response?.message ?? 'Не удалось изменить привязку REALITY'
}

export function RealityPresetModeSelector({ open, coreId, inboundSaved, inboundTag, initialPresetId, hasUnsavedChanges, mode, onModeChange, onApplied }: Props) {
  const { admin } = useAdmin()
  const canUpdate = hasPermission(admin, 'cores', 'update')
  const [presets, setPresets] = useState<RealityPreset[]>([])
  const [binding, setBinding] = useState<string | null>(null)
  const [selected, setSelected] = useState('')
  const [replacementKey, setReplacementKey] = useState('')
  const [pending, setPending] = useState(false)
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
  useEffect(() => { setReplacementKey('') }, [open, coreId, inboundTag])

  const currentPreset = useMemo(() => presets.find(item => item.id === binding), [presets, binding])
  // The list may be populated after the select mounts; keep the confirmed binding selected by default.
  const selectedValue = effectiveRealityPresetSelection(selected, binding)
  const selectedPreset = useMemo(() => presets.find(item => item.id === selectedValue), [presets, selectedValue])
  const verified = loadedScope === scope && !loading && !loadFailed
  const ready = verified && canUpdate && coreId !== null && inboundSaved && !hasUnsavedChanges && !pending
  const mismatch = verified && (initialPresetId ?? null) !== binding

  const changePreset = async () => {
    if (!ready || !selectedValue || !coreId || mismatch) return
    setPending(true)
    try {
      await $fetch('/api/core-presets/bindings', {
        method: 'POST', body: { core_id: coreId, inbound_tag: inboundTag, preset_id: selectedValue },
      })
      await queryClient.invalidateQueries({ queryKey: getListCoreBindingsQueryKey() })
      await queryClient.invalidateQueries({ queryKey: getGetCoreConfigQueryKey(coreId) })
      toast.success('Пресет REALITY привязан к ядру. Изменения для Node требуют отдельного применения.')
      onApplied()
    } catch (error) {
      toast.error(readableError(error))
    } finally { setPending(false) }
  }

  const switchToManual = async () => {
    if (!ready || !coreId || !binding || !replacementKey.trim() || mismatch) return
    if (!window.confirm('Снять привязку к защищённому пресету и сохранить новый приватный ключ прямо в конфигурации ядра?')) return
    setPending(true)
    try {
      await $fetch('/api/core-presets/bindings/reality/manual', {
        method: 'POST', body: { core_id: coreId, inbound_tag: inboundTag, private_key: replacementKey.trim() },
      })
      setReplacementKey('')
      await queryClient.invalidateQueries({ queryKey: getListCoreBindingsQueryKey() })
      await queryClient.invalidateQueries({ queryKey: getGetCoreConfigQueryKey(coreId) })
      toast.success('Режим REALITY переключён на ручную настройку.')
      onApplied()
    } catch (error) {
      toast.error(readableError(error))
    } finally { setPending(false) }
  }

  return (
    <div className="space-y-3 rounded-md border bg-muted/20 p-3 sm:col-span-2" data-testid="reality-preset-mode-selector">
      <div className="space-y-1">
        <p className="text-sm font-medium">Источник ключей REALITY</p>
        <p className="text-xs text-muted-foreground">Вручную — ключи хранятся в конфигурации ядра. Пресет — приватный ключ зашифрован в Backend и не раскрывается редактору.</p>
      </div>
      <Tabs value={mode} onValueChange={value => onModeChange(value === 'preset' ? 'preset' : 'manual')}>
        <TabsList className="grid w-full grid-cols-2">
          <TabsTrigger value="manual">Ручная настройка</TabsTrigger>
          <TabsTrigger value="preset">Выбрать пресет</TabsTrigger>
        </TabsList>
      </Tabs>
      {(!verified && !loadFailed) && <p className="text-xs text-muted-foreground">Загрузка пресетов и привязок…</p>}
      {loadFailed && <div className="space-y-2"><p className="text-xs text-destructive">Не удалось проверить привязки. Изменения запрещены.</p><Button type="button" variant="outline" size="sm" onClick={() => void refresh()}>Повторить</Button></div>}
      {mismatch && <p className="text-xs text-destructive">Сохранённая ссылка REALITY не совпадает с привязкой на сервере. Обновите страницу, прежде чем менять режим.</p>}
      {(coreId === null || !inboundSaved) && <p className="text-xs text-muted-foreground">Для привязки пресета сначала сохраните inbound и ядро в ручном режиме. Затем откройте inbound снова.</p>}
      {!canUpdate && <p className="text-xs text-muted-foreground">Для изменения режима требуется разрешение на редактирование ядер.</p>}
      {hasUnsavedChanges && <p className="text-xs text-amber-600">Сохраните или отмените текущие изменения ядра перед сменой источника ключей.</p>}
      {mode === 'manual' && !binding && !initialPresetId && <p className="text-xs text-muted-foreground">Настройте приватный ключ и Short IDs в полях ниже. Генерация новой пары доступна здесь же.</p>}
      {mode === 'manual' && binding && verified && (
        <div className="space-y-2">
          <p className="text-xs text-muted-foreground">Сейчас привязан пресет «{currentPreset?.name ?? binding}». Для перехода в ручной режим нужен новый приватный ключ; прежний секрет прочитать нельзя. При смене ключа существующим клиентам понадобится обновлённая подписка.</p>
          <Input type="password" autoComplete="off" aria-label="Новый приватный ключ REALITY" placeholder="Новый приватный ключ X25519" value={replacementKey} onChange={event => setReplacementKey(event.target.value)} />
          <div className="flex flex-wrap gap-2">
            <Button type="button" variant="outline" size="sm" onClick={() => setReplacementKey(generateRealityKeyPair().privateKey)}>Сгенерировать новый ключ</Button>
            <Button type="button" size="sm" disabled={!ready || !replacementKey.trim() || mismatch} onClick={() => void switchToManual()}>Перейти на ручную настройку</Button>
          </div>
        </div>
      )}
      {mode === 'preset' && (
        <div className="space-y-2">
          <Select value={selectedValue} onValueChange={setSelected} disabled={!verified}>
            <SelectTrigger aria-label="Пресет REALITY"><SelectValue placeholder="Выберите REALITY-пресет" /></SelectTrigger>
            <SelectContent>{presets.map(item => <SelectItem key={item.id} value={item.id}>{item.name} · v{item.revision}</SelectItem>)}</SelectContent>
          </Select>
          {selectedPreset && (
            <div className="space-y-1 text-xs text-muted-foreground">
              <p>Публичный ключ: <code className="break-all">{selectedPreset.data.publicKey ?? '—'}</code></p>
              <p>Short IDs: {(selectedPreset.data.shortIds ?? []).join(', ') || '—'}</p>
              <p>Приватный ключ хранится зашифрованным; для замены ключей отредактируйте пресет.</p>
            </div>
          )}
          {presets.length === 0 && verified && <p className="text-xs text-muted-foreground">REALITY-пресетов пока нет.</p>}
          <div className="flex flex-wrap items-center gap-2">
            <Button type="button" disabled={!ready || !selectedValue || selectedValue === binding || mismatch} size="sm" onClick={() => void changePreset()}>{binding ? 'Заменить привязку' : 'Привязать пресет'}</Button>
            <Button type="button" size="sm" variant="outline" onClick={() => void refresh()}>Обновить список</Button>
            <a className="text-xs text-primary underline" href="#/nodes/cores?presets=reality" target="_blank" rel="noopener noreferrer">Создать / управлять ключами и пресетами</a>
          </div>
          {binding && selectedValue === binding && verified && <p className="text-xs text-muted-foreground">Этот пресет уже привязан к inbound.</p>}
          <p className="text-xs text-muted-foreground">Для ядра, назначенного Node, изменение привязки запрещено сервером. Сначала потребуется безопасное переназначение профиля.</p>
        </div>
      )}
    </div>
  )
}
