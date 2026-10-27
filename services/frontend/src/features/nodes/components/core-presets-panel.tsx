import { useCallback, useEffect, useMemo, useState } from 'react'
import { toast } from 'sonner'
import { $fetch } from '@/service/http'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'

type Kind = 'reality' | 'vless' | 'routing'
type Preset = {
  id: string
  name: string
  kind: Kind
  data: Record<string, unknown>
  revision: number
}
type CoreTarget = {
  core_id: number
  core_name: string
  inbounds: { tag: string; protocol: string; security: string }[]
}
type Binding = { id: string; preset_id: string; core_id: number; kind: Kind; inbound_tag: string }
const endpoint = '/api/core-presets'
const errorText = (error: unknown) => error instanceof Error ? error.message : 'Операция не выполнена'

function valuesFor(kind: Kind): Record<string, string> {
  return kind === 'reality'
    ? { private_key: '', public_key: '', short_ids: 'aabb', mldsa65_seed: '', mldsa65_verify: '' }
    : kind === 'vless'
      ? { decryption: '', encryption: '' }
      : { routing: '{\n  "domainStrategy": "AsIs",\n  "rules": []\n}' }
}

export function CorePresetsPanel({ canCreate, canUpdate, canDelete }: {
  canCreate: boolean
  canUpdate: boolean
  canDelete: boolean
}) {
  const [presets, setPresets] = useState<Preset[]>([])
  const [cores, setCores] = useState<CoreTarget[]>([])
  const [bindings, setBindings] = useState<Binding[]>([])
  const [kind, setKind] = useState<Kind>('reality')
  const [editing, setEditing] = useState('')
  const [name, setName] = useState('')
  const [fields, setFields] = useState<Record<string, string>>(valuesFor('reality'))
  const [selectedCore, setSelectedCore] = useState('')
  const [selectedInbound, setSelectedInbound] = useState('')
  const [busy, setBusy] = useState(false)
  const [expanded, setExpanded] = useState(false)
  const [applyNeeded, setApplyNeeded] = useState<number[]>([])
  const load = useCallback(async () => {
    const [items, targets, attached] = await Promise.all([
      $fetch<Preset[]>(endpoint),
      $fetch<CoreTarget[]>(endpoint + '/targets'),
      $fetch<Binding[]>(endpoint + '/bindings'),
    ])
    setPresets(items)
    setCores(targets)
    setBindings(attached)
  }, [])
  useEffect(() => { void load().catch(error => toast.error(errorText(error))) }, [load])
  const activeCore = useMemo(() => cores.find(c => String(c.core_id) === selectedCore), [cores, selectedCore])
  const availableInbounds = useMemo(() => {
    if (kind === 'routing') return [{ tag: '__routing__', protocol: 'routing', security: '' }]
    return (activeCore?.inbounds ?? []).filter(item =>
      kind === 'vless' ? item.protocol === 'vless' : item.security === 'reality',
    )
  }, [activeCore, kind])
  const filteredPresets = presets.filter(p => p.kind === kind)
  const editField = (field: string, value: string) => setFields(current => ({ ...current, [field]: value }))
  function reset(typ: Kind = kind) {
    setEditing('')
    setName('')
    setFields(valuesFor(typ))
    setSelectedInbound('')
  }
  function chooseKind(typ: Kind) {
    setKind(typ)
    reset(typ)
  }
  function selectPreset(preset: Preset) {
    setEditing(preset.id)
    setName(preset.name)
    const current = valuesFor(preset.kind)
    for (const key of Object.keys(current)) {
      if (key === 'private_key' || key === 'decryption' || key === 'mldsa65_seed') continue
      const mapped = key === 'short_ids' ? 'shortIds' : key === 'public_key' ? 'publicKey'
        : key === 'mldsa65_verify' ? 'mldsa65Verify' : key
      const input = (preset.data as Record<string, unknown>)[mapped]
      if (key === 'routing' && input && typeof input === 'object') current[key] = JSON.stringify(input, null, 2)
      else if (key === 'short_ids' && Array.isArray(input)) current[key] = input.join(', ')
      else if (typeof input === 'string') current[key] = input
    }
    setFields(current)
  }
  async function execute(task: () => Promise<void>) {
    setBusy(true)
    try { await task() } catch (error) { toast.error(errorText(error)) }
    finally { setBusy(false) }
  }
  function parseData(): Record<string, unknown> {
    if (kind === 'routing') return { routing: JSON.parse(fields.routing) }
    if (kind === 'vless') {
      if (!fields.decryption?.trim() || !fields.encryption?.trim()) throw new Error('Нужны обе согласованные строки Xray VLESS Encryption')
      return { decryption: fields.decryption.trim(), encryption: fields.encryption.trim() }
    }
    if (!fields.private_key?.trim()) throw new Error('Укажите приватный ключ REALITY для создания или замены пресета')
    return {
      private_key: fields.private_key.trim(),
      public_key: fields.public_key?.trim() || undefined,
      short_ids: (fields.short_ids || '').split(',').map(value => value.trim()),
      mldsa65_seed: fields.mldsa65_seed?.trim() || undefined,
      mldsa65_verify: fields.mldsa65_verify?.trim() || undefined,
    }
  }
  function savePreset() {
    if (!name.trim()) { toast.error('Название обязательно'); return }
    void execute(async () => {
      const payload = { name: name.trim(), kind, data: parseData() }
      const result = await $fetch<{ affected_core_ids?: number[] }>(editing ? endpoint + '/' + editing : endpoint, {
        method: editing ? 'PUT' : 'POST', body: payload,
      })
      setApplyNeeded(result.affected_core_ids ?? [])
      toast.success(editing ? 'Пресет заменён; привязанные ноды необходимо обновить' : 'Пресет создан')
      reset()
      await load()
    })
  }
  function removePreset(preset: Preset) {
    if (!window.confirm('Удалить пресет «' + preset.name + '»? Привязанные пресеты удалить нельзя.')) return
    void execute(async () => {
      await $fetch(endpoint + '/' + preset.id, { method: 'DELETE' })
      if (editing === preset.id) reset()
      await load()
      toast.success('Пресет удалён')
    })
  }
  function attach() {
    const chosen = presets.find(p => p.id === editing)
    if (!chosen || !selectedCore || !selectedInbound) {
      toast.error('Выберите существующий пресет, ядро и inbound'); return
    }
    void execute(async () => {
      await $fetch(endpoint + '/bindings', {
        method: 'POST',
        body: { preset_id: chosen.id, core_id: Number(selectedCore), inbound_tag: selectedInbound },
      })
      setApplyNeeded([Number(selectedCore)])
      await load()
      toast.success('Пресет привязан. Изменения применятся после перезапуска нод ядра.')
    })
  }
  function applyCore(coreId: number) {
    if (!window.confirm('Перезапустить все ноды, использующие ядро #' + coreId + '? Активные соединения могут прерваться.')) return
    void execute(async () => {
      await $fetch('/api/core/' + coreId + '/restart', { method: 'POST' })
      setApplyNeeded(ids => ids.filter(x => x !== coreId))
      toast.success('Команда обновления нод отправлена')
    })
  }

  return <Card className="mb-5">
    <CardHeader className="flex flex-col items-stretch gap-3 sm:flex-row sm:items-center sm:justify-between">
      <div className="min-w-0 flex-1">
        <CardTitle>Пресеты Xray: REALITY, VLESS Encryption, маршрутизация</CardTitle>
        <p className="mt-1 text-sm text-muted-foreground">
          Ключи сохраняются в защищённом хранилище и подставляются только при запуске ноды.
          В конфигурации ядра остаются ссылки и публичные данные.
        </p>
      </div>
      <Button className="w-full shrink-0 sm:w-auto" variant="outline" onClick={() => setExpanded(value => !value)}>
        {expanded ? 'Свернуть' : 'Открыть пресеты'}
      </Button>
    </CardHeader>
    {expanded && <CardContent className="space-y-5">
      <div className="flex flex-wrap gap-2">
        {(['reality', 'vless', 'routing'] as const).map(type => (
          <Button key={type} size="sm" variant={kind === type ? 'default' : 'outline'} onClick={() => chooseKind(type)}>
            {type === 'reality' ? 'REALITY' : type === 'vless' ? 'VLESS Encryption' : 'Routing'}
          </Button>
        ))}
      </div>
      <div className="grid gap-2 sm:grid-cols-2">
        {filteredPresets.map(p => <div key={p.id} className="flex items-center justify-between gap-2 rounded-md border p-3">
          <div>
            <p className="text-sm font-medium">{p.name}</p>
            <p className="text-xs text-muted-foreground">Версия {p.revision} · {bindings.filter(b => b.preset_id === p.id).length} привязок</p>
          </div>
          <div className="flex gap-2">
            <Button size="sm" variant="outline" onClick={() => selectPreset(p)}>Выбрать</Button>
            {canDelete && <Button size="sm" variant="destructive" disabled={busy} onClick={() => removePreset(p)}>Удалить</Button>}
          </div>
        </div>)}
      </div>
      {filteredPresets.length === 0 && <p className="text-sm text-muted-foreground">Пресетов этого типа ещё нет.</p>}
      {(canCreate || canUpdate) && <div className="space-y-3 rounded-md border p-4">
        <div className="flex items-center justify-between">
          <p className="font-medium">{editing ? 'Обновить выбранный пресет' : 'Новый пресет'}</p>
          {editing && <Button variant="outline" size="sm" onClick={() => reset()}>Очистить выбор</Button>}
        </div>
        <Input placeholder="Название пресета" value={name} maxLength={120}
          onChange={event => setName(event.target.value)} />
        {kind === 'reality' && <>
          <label className="block text-sm">
            Приватный ключ REALITY (сервер, хранится зашифрованным)
            <Textarea className="mt-1 font-mono text-xs" value={fields.private_key ?? ''} placeholder="Вставьте приватный ключ Xray x25519"
              onChange={event => editField('private_key', event.target.value)} />
          </label>
          <label className="block text-sm">Публичный ключ (необязательно, будет проверен)
            <Input className="mt-1 font-mono text-xs" value={fields.public_key ?? ''}
              onChange={event => editField('public_key', event.target.value)} />
          </label>
          <label className="block text-sm">Short IDs (через запятую)
            <Input className="mt-1 font-mono" value={fields.short_ids ?? ''}
              onChange={event => editField('short_ids', event.target.value)} />
          </label>
          <label className="block text-sm">ML-DSA65 seed (необязательно, секрет)
            <Textarea className="mt-1 font-mono text-xs" value={fields.mldsa65_seed ?? ''}
              onChange={event => editField('mldsa65_seed', event.target.value)} />
          </label>
          <label className="block text-sm">ML-DSA65 verify (необязательно, публичный)
            <Input className="mt-1 font-mono text-xs" value={fields.mldsa65_verify ?? ''}
              onChange={event => editField('mldsa65_verify', event.target.value)} />
          </label>
        </>}
        {kind === 'vless' && <>
          <p className="text-xs text-muted-foreground">
            Скопируйте пару из генератора Xray vlessenc: серверное decryption и клиентское encryption.
            Для VMess отдельной пары этих ключей нет.
          </p>
          <label className="block text-sm">Decryption (сервер, секрет)
            <Textarea className="mt-1 font-mono text-xs" value={fields.decryption ?? ''}
              onChange={event => editField('decryption', event.target.value)} />
          </label>
          <label className="block text-sm">Encryption (клиент)
            <Textarea className="mt-1 font-mono text-xs" value={fields.encryption ?? ''}
              onChange={event => editField('encryption', event.target.value)} />
          </label>
        </>}
        {kind === 'routing' && <>
          <p className="text-xs text-muted-foreground">
            Только секция routing с rules и balancers; outbounds определяются в самом ядре.
          </p>
          <label className="block text-sm">Routing JSON
            <Textarea rows={9} className="mt-1 font-mono text-xs" value={fields.routing ?? ''}
              onChange={event => editField('routing', event.target.value)} />
          </label>
        </>}
        <Button disabled={busy || !(editing ? canUpdate : canCreate)} onClick={savePreset}>
          {editing ? 'Заменить значения пресета' : 'Создать пресет'}
        </Button>
      </div>}
      {canUpdate && <div className="space-y-3 rounded-md border p-4">
        <p className="font-medium">Привязать выбранный пресет к ядру</p>
        <p className="text-xs text-muted-foreground">
          Привязка заменяет inline-настройки выбранного inbound. В БД и ответе API приватный ключ больше не отображается.
          Изменения в работающих нодах применяются только по отдельному подтверждению.
        </p>
        <label className="block text-sm">Ядро
          <select className="bg-background mt-1 h-10 w-full rounded-md border px-2" value={selectedCore}
            onChange={event => { setSelectedCore(event.target.value); setSelectedInbound(kind === 'routing' ? '__routing__' : '') }}>
            <option value="">Выберите ядро</option>
            {cores.map(item => <option key={item.core_id} value={String(item.core_id)}>{item.core_name} (#{item.core_id})</option>)}
          </select>
        </label>
        <label className="block text-sm">Точка применения
          <select className="bg-background mt-1 h-10 w-full rounded-md border px-2" value={selectedInbound}
            onChange={event => setSelectedInbound(event.target.value)}>
            <option value="">Выберите inbound</option>
            {availableInbounds.map(item => <option key={item.tag} value={item.tag}>{item.tag}</option>)}
          </select>
        </label>
        <Button disabled={busy || !editing || !selectedCore || !selectedInbound} onClick={attach}>Привязать / заменить привязку</Button>
      </div>}
      <div>
        <p className="mb-2 font-medium">Использование пресетов</p>
        <div className="space-y-2 text-sm">
          {bindings.filter(b => b.kind === kind).map(binding => (
            <div className="flex flex-wrap items-center justify-between gap-2 rounded-md border p-2" key={binding.id}>
              <span>Ядро #{binding.core_id} · {binding.inbound_tag} · {presets.find(p => p.id === binding.preset_id)?.name ?? binding.preset_id}</span>
              {canUpdate && <Button variant="outline" size="sm" onClick={() => applyCore(binding.core_id)}>Применить на ноды</Button>}
            </div>
          ))}
        </div>
        {applyNeeded.length > 0 && <p className="mt-2 text-sm text-amber-600">
          Есть неприменённые изменения для ядер: {applyNeeded.join(', ')}. Обновите связанные ноды после проверки.
        </p>}
      </div>
    </CardContent>}
  </Card>
}
