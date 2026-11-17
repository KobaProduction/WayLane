import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link, useSearchParams } from 'react-router'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'
import { generateRealityKeyPair, generateRealityShortId } from '@/lib/xray-generation'
import { $fetch } from '@/service/http'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import {
  AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent,
  AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle, AlertDialogTrigger,
} from '@/components/ui/alert-dialog'
import {
  hasNewPresetMaterial, newPresetFields, presetDraftChanged, presetPublicInfo, safeExistingPresetFields,
  validCorePresetKind, validCorePresetTab,
} from './core-preset-workspace'
import type { CorePresetKind, CorePresetTab } from './core-preset-workspace'

type Preset = { id: string; name: string; kind: CorePresetKind; data: Record<string, unknown>; revision: number }
type CoreTarget = { core_id: number; core_name: string; inbounds: Array<{ tag: string; protocol: string; security: string }> }
type Binding = { id: string; preset_id: string; core_id: number; kind: CorePresetKind; inbound_tag: string }
const endpoint = '/api/core-presets'
const displayKind: Record<CorePresetKind, string> = { reality: 'REALITY', vless: 'VLESS Encryption', routing: 'Routing' }

function requestErrorKey(error: unknown): string {
  const value = error as { status?: number }
  if (value?.status === 409) return 'errorConflict'
  if (value?.status === 422) return 'errorInvalid'
  return 'errorGeneral'
}

export function CorePresetsPanel({ canCreate, canUpdate, canDelete }: {
  canCreate: boolean; canUpdate: boolean; canDelete: boolean
}) {
  const { t } = useTranslation()
  const tx = (key: string) => t(`nodes.presets.workspace.${key}`)
  const [params, setParams] = useSearchParams()
  const kind = validCorePresetKind(params.get('kind') ?? params.get('presets'))
  const tab = validCorePresetTab(params.get('tab'))
  const selectedId = params.get('preset') ?? ''
  const [presets, setPresets] = useState<Preset[]>([])
  const [cores, setCores] = useState<CoreTarget[]>([])
  const [bindings, setBindings] = useState<Binding[]>([])
  const [name, setName] = useState('')
  const [fields, setFields] = useState<Record<string, string>>(() => newPresetFields(kind))
  const [selectedCore, setSelectedCore] = useState('')
  const [selectedInbound, setSelectedInbound] = useState('')
  const [busy, setBusy] = useState(false)
  const [pendingDiscard, setPendingDiscard] = useState<(() => void) | null>(null)
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState('')
  const [applyNeeded, setApplyNeeded] = useState<number[]>([])
  const [queued, setQueued] = useState<Record<number, number>>({})
  const draftSource = useRef<string>('')

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const [items, targets, attached] = await Promise.all([
        $fetch<Preset[]>(endpoint),
        $fetch<CoreTarget[]>(endpoint + '/targets'),
        $fetch<Binding[]>(endpoint + '/bindings'),
      ])
      setPresets(items)
      setCores(targets)
      setBindings(attached)
      setLoadError('')
    } catch (error) {
      setLoadError(requestErrorKey(error))
    } finally {
      setLoading(false)
    }
  }, [])
  useEffect(() => { void load() }, [load])

  const selected = presets.find(item => item.id === selectedId && item.kind === kind)
  const filtered = presets.filter(item => item.kind === kind)
  const presetBindings = bindings.filter(binding => binding.preset_id === selected?.id)
  const availableInbounds = useMemo(() => {
    if (kind === 'routing') return [{ tag: '__routing__', protocol: 'routing', security: '' }]
    const target = cores.find(core => String(core.core_id) === selectedCore)
    return (target?.inbounds ?? []).filter(item => kind === 'vless' ? item.protocol === 'vless' : item.security === 'reality')
  }, [kind, cores, selectedCore])

  useEffect(() => {
    if (!selected) return
    const source = `${selected.id}:${selected.revision}`
    if (draftSource.current === source) return
    draftSource.current = source
    setName(selected.name)
    setFields(safeExistingPresetFields(selected.kind, selected.data))
  }, [selected])

  function navigate(next: CorePresetTab, id = selectedId, typ = kind) {
    const query = new URLSearchParams()
    query.set('kind', typ)
    if (next !== 'catalog') query.set('tab', next)
    if (id) query.set('preset', id)
    setParams(query)
  }

  function confirmDiscardFor(change: () => void) {
    if (presetDraftChanged(kind, name, fields, selected)) setPendingDiscard(() => change)
    else change()
  }

  function chooseKind(next: CorePresetKind) {
    setFields(newPresetFields(next));setName('');draftSource.current=''
    setSelectedCore('');setSelectedInbound('')
    navigate('catalog', '', next)
  }

  function startNew() {
    draftSource.current = ''
    setName('');setFields(newPresetFields(kind));setSelectedCore('');setSelectedInbound('')
    navigate('editor', '')
  }

  function selectPreset(preset: Preset, next: CorePresetTab = 'details') {
    setName(preset.name)
    setFields(safeExistingPresetFields(preset.kind, preset.data))
    draftSource.current = `${preset.id}:${preset.revision}`
    setSelectedCore('');setSelectedInbound('')
    navigate(next, preset.id, preset.kind)
  }

  async function execute(task: () => Promise<void>) {
    if (busy) return
    setBusy(true)
    try { await task() } catch (error) { toast.error(tx(requestErrorKey(error))) }
    finally { setBusy(false) }
  }

  function parseData(): Record<string, unknown> {
    if (kind === 'routing') return { routing: JSON.parse(fields.routing) }
    if (kind === 'vless') return { decryption: fields.decryption.trim(), encryption: fields.encryption.trim() }
    return {
      private_key: fields.private_key.trim(),
      public_key: fields.public_key?.trim() || undefined,
      short_ids: (fields.short_ids || '').split(',').map(value => value.trim()).filter(Boolean),
      mldsa65_seed: fields.mldsa65_seed?.trim() || undefined,
      mldsa65_verify: fields.mldsa65_verify?.trim() || undefined,
    }
  }

  function saveMaterial() {
    if (!name.trim() || !hasNewPresetMaterial(kind, fields)) return
    void execute(async () => {
      const previous = selected
      const result = await $fetch<{ id: string; affected_core_ids?: number[] }>(previous ? endpoint + '/' + previous.id : endpoint, {
        method: previous ? 'PUT' : 'POST',
        body: { name: name.trim(), kind, data: parseData(), ...(previous ? { expected_revision: previous.revision } : {}) },
      })
      if (result.affected_core_ids?.length) setApplyNeeded(ids => [...new Set([...ids, ...result.affected_core_ids!])])
      toast.success(previous ? tx('rotated') : tx('created'))
      // Never keep secrets from an accepted request in the UI after the save.
      setFields(newPresetFields(kind));setName('');draftSource.current = ''
      await load()
      navigate('details', result.id)
    })
  }

  function renamePreset() {
    if (!selected || !name.trim() || name.trim() === selected.name) return
    void execute(async () => {
      await $fetch(endpoint + '/' + selected.id + '/name', {
        method: 'PATCH', body: { name: name.trim(), expected_revision: selected.revision },
      })
      toast.success(tx('renamed'))
      await load()
    })
  }

  function removePreset(preset: Preset) {
    if (bindings.some(binding => binding.preset_id === preset.id)) return
    void execute(async () => {
      await $fetch(endpoint + '/' + preset.id, { method: 'DELETE' })
      toast.success(tx('deleted'))
      draftSource.current='';setFields(newPresetFields(kind));setName('')
      navigate('catalog','')
      await load()
    })
  }

  function attach() {
    if (!selected || !selectedCore || !selectedInbound) return
    void execute(async () => {
      await $fetch(endpoint + '/bindings', {
        method: 'POST',
        body: { preset_id: selected.id, core_id: Number(selectedCore), inbound_tag: selectedInbound },
      })
      setApplyNeeded(ids => [...new Set([...ids, Number(selectedCore)])])
      toast.success(tx('bound'))
      await load()
    })
  }

  function applyCore(coreId: number) {
    void execute(async () => {
      const result = await $fetch<{ queued_nodes: number; status: string }>(`/api/core/${coreId}/apply`, { method: 'POST' })
      setQueued(current => ({ ...current, [coreId]: result.queued_nodes }))
      setApplyNeeded(ids => ids.filter(id => id !== coreId))
      if (result.queued_nodes > 0) toast.success(`${tx('queuedSuccess')}: ${result.queued_nodes}. ${tx('queuedVerify')}`)
      else toast.info(tx('queuedNone'))
    })
  }

  const materialReady = Boolean(name.trim() && hasNewPresetMaterial(kind, fields))
  const canManage = selected ? canUpdate : canCreate
  return <div className="min-w-0 space-y-4" data-testid="xray-presets-workspace">
    <header className="space-y-1">
      <h2 className="text-lg font-semibold">{tx('title')}</h2>
      <p className="text-sm text-muted-foreground">{tx('description')}</p>
    </header>
    <div className="flex flex-wrap gap-2" aria-label={tx('kindLabel')}>
      {(['reality', 'vless', 'routing'] as const).map(typ => <Button type="button" key={typ}
        size="sm" variant={kind === typ ? 'default' : 'outline'} aria-pressed={kind === typ} onClick={() => typ !== kind && confirmDiscardFor(() => chooseKind(typ))}>
        {displayKind[typ]}
      </Button>)}
    </div>
    {loadError && <div role="alert" className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-destructive/40 p-3 text-sm text-destructive">
      <span>{tx('loadFailed')}: {tx(loadError)}</span>
      <Button type="button" size="sm" variant="outline" disabled={loading} onClick={() => void load()}>{tx('reload')}</Button>
    </div>}
    <Tabs value={tab} onValueChange={value => navigate(validCorePresetTab(value))} className="space-y-4">
      <TabsList className="h-auto w-full flex-wrap justify-start gap-1" aria-label={tx('tabsLabel')}>
        <TabsTrigger value="catalog" className="min-h-10 flex-1 sm:flex-none">{tx('catalog')}</TabsTrigger>
        <TabsTrigger value="details" className="min-h-10 flex-1 sm:flex-none">{tx('details')}</TabsTrigger>
        {(canCreate || canUpdate) && <TabsTrigger value="editor" className="min-h-10 flex-1 sm:flex-none">{selected ? tx('revisionNew') : tx('createTab')}</TabsTrigger>}
        <TabsTrigger value="bindings" className="min-h-10 flex-1 sm:flex-none">{tx('bindingsTab')}</TabsTrigger>
      </TabsList>
      <TabsContent value="catalog" className="space-y-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <p className="text-sm text-muted-foreground">{displayKind[kind]} · {tx('found')}: {filtered.length}</p>
          {canCreate && <Button size="sm" onClick={() => confirmDiscardFor(startNew)}>{tx('create')}</Button>}
        </div>
        {loading && filtered.length === 0 && <p role="status" className="text-sm text-muted-foreground">{tx('loading')}</p>}
        {!loading && !loadError && filtered.length === 0 && <p role="status" className="rounded-lg border border-dashed p-6 text-center text-sm text-muted-foreground">{tx('empty')}</p>}
        <div className="grid gap-3 lg:grid-cols-2">
          {filtered.map(item => <article key={item.id} className="min-w-0 space-y-3 rounded-lg border p-4">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <p className="break-words font-medium">{item.name}</p>
              <Badge variant="outline">{tx('revision')} {item.revision}</Badge>
            </div>
            <p className="text-sm text-muted-foreground">{tx('boundCount')}: {bindings.filter(binding => binding.preset_id === item.id).length}</p>
            <div className="flex flex-wrap gap-2">
              <Button size="sm" variant="outline" onClick={() => confirmDiscardFor(() => selectPreset(item))}>{tx('details')}</Button>
              <Button size="sm" variant="ghost" onClick={() => confirmDiscardFor(() => selectPreset(item,'bindings'))}>{tx('usage')}</Button>
            </div>
          </article>)}
        </div>
      </TabsContent>
      <TabsContent value="details" className="space-y-4">
        {!selected ? <p role="status" className="rounded-lg border border-dashed p-6 text-sm text-muted-foreground">{tx('selectHint')}</p>
          : <section className="min-w-0 space-y-4 rounded-lg border p-4" aria-label={tx('selectedLabel')}>
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div className="min-w-0"><h3 className="break-words text-base font-semibold">{selected.name}</h3><p className="text-sm text-muted-foreground">{displayKind[selected.kind]} · {tx('revision')} {selected.revision}</p></div>
              <Badge variant="outline">{presetBindings.length} {tx('bindingsCount')}</Badge>
            </div>
            <dl className="space-y-3 border-t pt-3">
              {presetPublicInfo(kind, selected.data).map(item => <div key={item.label} className="min-w-0 space-y-1 text-sm">
                <dt className="font-medium text-muted-foreground">{({
                  'Публичный X25519': tx('publicX25519'),
                  'Short IDs': tx('publicShortIds'),
                  'ML-DSA65 verify': tx('publicMldsa'),
                  'VLESS Encryption (клиент)': tx('publicVless'),
                  'Routing JSON (без приватных ключей)': tx('publicRouting'),
                } as Record<string, string>)[item.label] ?? item.label}</dt>
                <dd className="break-all rounded-md bg-muted/30 p-2 font-mono text-xs">{item.value}</dd>
              </div>)}
            </dl>
            {canUpdate && <div className="space-y-2 border-t pt-3">
              <label className="block space-y-2 text-sm"><span>{tx('name')}</span>
                <Input aria-label={tx('renameInput')} value={name} maxLength={120} onChange={e => setName(e.target.value)} />
              </label>
              <div className="flex flex-wrap gap-2">
                <Button size="sm" variant="outline" disabled={busy || !name.trim() || name.trim() === selected.name} onClick={renamePreset}>{tx('rename')}</Button>
                <Button size="sm" variant="outline" onClick={() => confirmDiscardFor(() => { setName(selected.name); setFields(safeExistingPresetFields(kind,selected.data)); navigate('editor') })}>{tx('newRevision')}</Button>
              </div>
            </div>}
            <div className="flex flex-wrap items-center justify-between gap-2 border-t pt-3">
              <Button type="button" size="sm" variant="outline" onClick={() => navigate('bindings')}>{tx('coreBindings')}</Button>
              {canDelete && <AlertDialog>
                <AlertDialogTrigger asChild><Button size="sm" variant="destructive" disabled={busy || presetBindings.length > 0}>{tx('delete')}</Button></AlertDialogTrigger>
                <AlertDialogContent>
                  <AlertDialogHeader><AlertDialogTitle>{t('nodes.presets.workspace.deleteTitle', { name: selected.name })}</AlertDialogTitle>
                    <AlertDialogDescription>{tx('deleteDescription')}</AlertDialogDescription>
                  </AlertDialogHeader>
                  <AlertDialogFooter><AlertDialogCancel>{tx('cancel')}</AlertDialogCancel>
                    <AlertDialogAction onClick={() => removePreset(selected)}>{tx('deleteConfirm')}</AlertDialogAction></AlertDialogFooter>
                </AlertDialogContent>
              </AlertDialog>}
            </div>
          </section>}
      </TabsContent>
      <TabsContent value="editor" className="space-y-4">
        <section className="min-w-0 space-y-4 rounded-lg border p-4" aria-label={tx('editorLabel')}>
          <div className="space-y-1">
            <h3 className="text-base font-semibold">{selected ? t('nodes.presets.workspace.revisionHeading', { name: selected.name }) : tx('createHeading')}</h3>
            {selected && <p className="text-sm text-muted-foreground">{tx('rotationWarning')}</p>}
          </div>
          <label className="block space-y-2 text-sm"><span>{tx('name')}</span>
            <Input aria-label={tx('newName')} value={name} maxLength={120} disabled={busy || !canManage} onChange={event => setName(event.target.value)} />
          </label>
          {kind === 'reality' && <div className="space-y-3">
            <div className="flex flex-wrap gap-2">
              <Button size="sm" variant="outline" type="button" disabled={busy || !canManage} onClick={() => {
                const pair = generateRealityKeyPair()
                setFields(current => ({...current,private_key:pair.privateKey,public_key:pair.publicKey}))
              }}>{tx('generateKey')}</Button>
              <Button size="sm" variant="outline" type="button" disabled={busy || !canManage} onClick={() => {
                const shortId = generateRealityShortId()
                setFields(current => ({...current,short_ids:[current.short_ids?.trim(),shortId].filter(Boolean).join(', ')}))
              }}>{tx('generateShortId')}</Button>
            </div>
            <label className="block space-y-2 text-sm"><span>{tx('privateKey')}</span>
              <Input type="password" autoComplete="new-password" aria-label={tx('privateKeyInput')} value={fields.private_key ?? ''} disabled={busy || !canManage} onChange={e => setFields(v => ({...v,private_key:e.target.value}))}/>
            </label>
            <label className="block space-y-2 text-sm"><span>{tx('publicKey')}</span>
              <Input className="font-mono text-xs" aria-label={tx('publicKeyInput')} value={fields.public_key ?? ''} disabled={busy || !canManage} onChange={e => setFields(v => ({...v,public_key:e.target.value}))}/>
            </label>
            <label className="block space-y-2 text-sm"><span>{tx('shortIds')}</span>
              <Input aria-label={tx('shortIdsInput')} value={fields.short_ids ?? ''} disabled={busy || !canManage} onChange={e => setFields(v => ({...v,short_ids:e.target.value}))}/>
            </label>
            <details className="space-y-3 rounded-md border p-3">
              <summary className="cursor-pointer text-sm font-medium">{tx('mldsaExtra')}</summary>
              <label className="mt-3 block space-y-2 text-sm"><span>{tx('mldsaSeed')}</span>
                <Input type="password" autoComplete="new-password" aria-label={tx('mldsaSeed')} value={fields.mldsa65_seed ?? ''} disabled={busy || !canManage} onChange={e => setFields(v => ({...v,mldsa65_seed:e.target.value}))}/>
              </label>
              <label className="mt-3 block space-y-2 text-sm"><span>{tx('mldsaVerify')}</span>
                <Input aria-label={tx('mldsaVerify')} value={fields.mldsa65_verify ?? ''} disabled={busy || !canManage} onChange={e => setFields(v => ({...v,mldsa65_verify:e.target.value}))}/>
              </label>
            </details>
          </div>}
          {kind === 'vless' && <div className="space-y-3">
            <p className="text-xs text-muted-foreground">{tx('vlessHint')}</p>
            <label className="block space-y-2 text-sm"><span>{tx('vlessDecryption')}</span>
              <Input type="password" autoComplete="new-password" aria-label={tx('vlessDecryption')} value={fields.decryption??''} disabled={busy || !canManage} onChange={e => setFields(v => ({...v,decryption:e.target.value}))}/>
            </label>
            <label className="block space-y-2 text-sm"><span>{tx('vlessEncryption')}</span>
              <Textarea aria-label={tx('vlessEncryption')} className="font-mono text-xs" value={fields.encryption??''} disabled={busy || !canManage} onChange={e => setFields(v => ({...v,encryption:e.target.value}))}/>
            </label>
          </div>}
          {kind === 'routing' && <label className="block space-y-2 text-sm"><span>{tx('routingJson')}</span>
            <Textarea rows={10} aria-label={tx('routingInput')} className="font-mono text-xs" value={fields.routing??''} disabled={busy || !canManage} onChange={e => setFields(v=>({...v,routing:e.target.value}))}/>
          </label>}
          <div className="flex flex-wrap items-center gap-2 border-t pt-3">
            {!selected ? <Button disabled={busy || !canCreate || !materialReady} onClick={saveMaterial}>{tx('create')}</Button> : <AlertDialog>
              <AlertDialogTrigger asChild>
                <Button variant="destructive" disabled={busy || !canUpdate || !materialReady}>{tx('saveRevision')}</Button>
              </AlertDialogTrigger>
              <AlertDialogContent>
                <AlertDialogHeader><AlertDialogTitle>{tx('replaceTitle')}</AlertDialogTitle>
                  <AlertDialogDescription>{t('nodes.presets.workspace.replaceDescription', { name: selected.name })}</AlertDialogDescription>
                </AlertDialogHeader>
                <AlertDialogFooter><AlertDialogCancel>{tx('cancel')}</AlertDialogCancel><AlertDialogAction onClick={saveMaterial}>{tx('replaceConfirm')}</AlertDialogAction></AlertDialogFooter>
              </AlertDialogContent>
            </AlertDialog>}
            <Button variant="outline" size="sm" onClick={() => {
              setName(selected?.name ?? '')
              setFields(selected ? safeExistingPresetFields(kind, selected.data) : newPresetFields(kind))
              navigate(selected ? 'details' : 'catalog')
            }}>{tx('cancel')}</Button>
          </div>
        </section>
      </TabsContent>
      <TabsContent value="bindings" className="space-y-4">
        <section className="space-y-3 rounded-lg border p-4">
          <h3 className="text-base font-semibold">{tx('bindingsHeading')}</h3>
          <label className="block space-y-2 text-sm"><span>{tx('preset')}</span>
            <select className="bg-background h-10 w-full rounded-md border px-3" aria-label={tx('presetSelect')} value={selected?.id ?? ''}
              onChange={e => {const item=presets.find(p=>p.id===e.target.value);if(item)confirmDiscardFor(() => selectPreset(item,'bindings'));else confirmDiscardFor(() => navigate('bindings',''))}}>
              <option value="">{tx('selectPreset')}</option>
              {filtered.map(item=><option key={item.id} value={item.id}>{item.name} · {tx('revision')} {item.revision}</option>)}
            </select>
          </label>
          {selected && <>
            <p className="text-sm text-muted-foreground">{tx('bindingHint')}</p>
            {canUpdate && <div className="grid min-w-0 gap-3 sm:grid-cols-2">
              <label className="space-y-2 text-sm"><span>{tx('savedCore')}</span>
                <select className="bg-background h-10 w-full rounded-md border px-2" value={selectedCore} aria-label={tx('coreSelect')}
                  onChange={e=>{setSelectedCore(e.target.value);setSelectedInbound(kind==='routing'?'__routing__':'')}}>
                  <option value="">{tx('chooseCore')}</option>
                  {cores.map(core=><option value={core.core_id} key={core.core_id}>{core.core_name}</option>)}
                </select>
              </label>
              <label className="space-y-2 text-sm"><span>{kind==='routing' ? tx('routing') : 'Inbound'}</span>
                <select className="bg-background h-10 w-full rounded-md border px-2" value={selectedInbound} aria-label={tx('inboundSelect')}
                  onChange={e=>setSelectedInbound(e.target.value)}>
                  <option value="">{tx('chooseInbound')}</option>
                  {availableInbounds.map(item=><option value={item.tag} key={item.tag}>{item.tag}</option>)}
                </select>
              </label>
            </div>}
            {canUpdate && <AlertDialog>
              <AlertDialogTrigger asChild>
                <Button disabled={busy || !selectedCore || !selectedInbound} size="sm">{tx('bindingButton')}</Button>
              </AlertDialogTrigger>
              <AlertDialogContent>
                <AlertDialogHeader><AlertDialogTitle>{tx('bindTitle')}</AlertDialogTitle>
                  <AlertDialogDescription>{tx('bindDescription')}</AlertDialogDescription>
                </AlertDialogHeader>
                <AlertDialogFooter><AlertDialogCancel>{tx('cancel')}</AlertDialogCancel><AlertDialogAction onClick={attach}>{tx('bindConfirm')}</AlertDialogAction></AlertDialogFooter>
              </AlertDialogContent>
            </AlertDialog>}
            <div className="space-y-2 border-t pt-3">
              <p className="font-medium">{tx('usedWhere')}</p>
              {presetBindings.length===0 && <p role="status" className="text-sm text-muted-foreground">{tx('unused')}</p>}
              {presetBindings.map(binding=> <div key={binding.id} className="min-w-0 space-y-2 rounded-md border p-3 text-sm">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <div className="min-w-0 space-y-1"><Link className="font-medium text-primary underline" to={`/nodes/cores/${binding.core_id}`}>{cores.find(c=>c.core_id===binding.core_id)?.core_name??`Core #${binding.core_id}`}</Link>
                    <p className="break-all text-xs text-muted-foreground">{binding.inbound_tag}</p></div>
                  <Badge variant="outline">{displayKind[binding.kind]}</Badge>
                </div>
                {canUpdate && <AlertDialog>
                  <AlertDialogTrigger asChild><Button type="button" variant="outline" size="sm" disabled={busy}>{tx('applyButton')}</Button></AlertDialogTrigger>
                  <AlertDialogContent>
                    <AlertDialogHeader><AlertDialogTitle>{tx('applyTitle')}</AlertDialogTitle>
                      <AlertDialogDescription>{tx('applyDescription')}</AlertDialogDescription>
                    </AlertDialogHeader>
                    <AlertDialogFooter><AlertDialogCancel>{tx('cancel')}</AlertDialogCancel><AlertDialogAction onClick={()=>applyCore(binding.core_id)}>{tx('queue')}</AlertDialogAction></AlertDialogFooter>
                  </AlertDialogContent>
                </AlertDialog>}
                {applyNeeded.includes(binding.core_id) && <p role="status" className="text-xs text-muted-foreground">{tx('changedHere')}</p>}
                {queued[binding.core_id] !== undefined && <p role="status" className="text-xs text-muted-foreground">
                  {tx('queuedCount')}: {queued[binding.core_id]}. {tx('queuedNote')}
                </p>}
              </div>)}
            </div>
          </>}
        </section>
      </TabsContent>
    </Tabs>
    <AlertDialog open={pendingDiscard !== null} onOpenChange={open => { if (!open) setPendingDiscard(null) }}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>{tx('discardTitle')}</AlertDialogTitle>
          <AlertDialogDescription>{tx('discardDescription')}</AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel>{tx('cancel')}</AlertDialogCancel>
          <AlertDialogAction onClick={() => {
            const action = pendingDiscard
            setPendingDiscard(null)
            action?.()
          }}>{tx('discardConfirm')}</AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  </div>
}
