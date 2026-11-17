import { ACMEPanel } from '@/features/tls/acme-panel'
import { useCallback, useEffect, useRef, useState } from 'react'
import { Link, useSearchParams } from 'react-router'
import { Tabs, TabsList, TabsTrigger, TabsContent } from '@/components/ui/tabs'
import { certificateExpiry, validTlsWorkspaceTab } from '@/features/tls/tls-workspace-tabs'
import { Badge } from '@/components/ui/badge'
import { AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle, AlertDialogTrigger } from '@/components/ui/alert-dialog'
import { $fetch } from '@/service/http'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'

type Preset = {
  id: string
  name: string
  certificate_pem: string
  fingerprint_sha256: string
  subject: string
  not_after: string
  revision: number
}
type Target = { node_uuid: string; node_name: string; core_id: number | null; inbound_tag: string; generation: number; runtime_status: string }
type Binding = { node_uuid: string; preset_id: string; preset_name: string; revision: number; inbound_tag: string }
type ApplyResult = { node_uuid: string; status: string }

const base = '/api/tls-presets'
const errorMessage = (reason: unknown) =>
  reason instanceof Error ? reason.message : 'Неизвестная ошибка сервера'

function TlsPresetPicker({ presets, value, onChange }: { presets: Preset[]; value: string; onChange: (id: string) => void }) {
  return <label className="block space-y-2 text-sm">
    <span>Выбрать TLS-сертификат</span>
    <select aria-label="TLS-пресет" className="bg-background w-full rounded-md border px-3 py-2"
      value={value} onChange={event => onChange(event.target.value)}>
      <option value="">Создать новый</option>
      {presets.map(item => <option key={item.id} value={item.id}>{item.name} · v{item.revision}</option>)}
    </select>
  </label>
}

export default function TlsPresetsPage() {
  const [searchParams, setSearchParams] = useSearchParams()
  const tab = validTlsWorkspaceTab(searchParams.get('tab'))
  const [acmeOpened, setAcmeOpened] = useState(() => tab === 'acme')
  const [presets, setPresets] = useState<Preset[]>([])
  const [loading, setLoading] = useState(true)
  const [loaded, setLoaded] = useState(false)
  const loadRevision = useRef(0)
  const [targets, setTargets] = useState<Target[]>([])
  const [allBindings, setAllBindings] = useState<Binding[]>([])
  const [activeId, setActiveId] = useState(() => searchParams.get('preset') ?? '')
  const [name, setName] = useState('')
  const [renamedName, setRenamedName] = useState('')
  const [certificate, setCertificate] = useState('')
  const [privateKey, setPrivateKey] = useState('')
  const [selectedTarget, setSelectedTarget] = useState('')
  const [webhookToken, setWebhookToken] = useState('')
  const [applyResult, setApplyResult] = useState<ApplyResult[]>([])
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [busy, setBusy] = useState(false)

  function selectTab(value: string) {
    const next = validTlsWorkspaceTab(value)
    setNotice('')
    if (next === 'acme') setAcmeOpened(true)
    const query = new URLSearchParams(searchParams)
    if (next === 'catalog') query.delete('tab')
    else query.set('tab', next)
    setSearchParams(query)
  }

  function selectPreset(id: string) {
    setActiveId(id)
    const query = new URLSearchParams(searchParams)
    if (id) query.set('preset', id)
    else query.delete('preset')
    setSearchParams(query, { replace: true })
  }

  function choosePreset(id: string) {
    setNotice('')
    selectPreset(id)
    setRenamedName(presets.find(item => item.id === id)?.name ?? '')
    setCertificate('')
    setPrivateKey('')
    setWebhookToken('')
    setApplyResult([])
    setSelectedTarget('')
  }

  const selected = presets.find(p => p.id === activeId)
  const expiry = certificateExpiry(selected?.not_after, Date.now())
  const bindings = allBindings.filter(binding => binding.preset_id === activeId)
  const load = useCallback(async () => {
    const revision = ++loadRevision.current
    setLoading(true)
    try {
      const [items, available] = await Promise.all([
        $fetch<Preset[]>(base),
        $fetch<{ targets: Target[]; bindings: Binding[] }>('/api/node-registry/tls-presets/targets'),
      ])
      if (revision !== loadRevision.current) return
      setPresets(items)
      setTargets(available.targets)
      setAllBindings(available.bindings)
      setLoaded(true)
      setError('')
      if (activeId && !items.some(p => p.id === activeId)) setActiveId('')
      if (activeId) setRenamedName(items.find(item => item.id === activeId)?.name ?? '')
    } catch (reason) {
      if (revision === loadRevision.current) setError(errorMessage(reason))
      throw reason
    } finally {
      if (revision === loadRevision.current) setLoading(false)
    }
  }, [activeId])

  useEffect(() => {
    void load().catch(() => {})
    return () => { loadRevision.current++ }
  }, [load])

  useEffect(() => {
    if (tab === 'acme') setAcmeOpened(true)
  }, [tab])

  async function run(work: () => Promise<void>) {
    setBusy(true)
    setError('')
    setNotice('')
    try {
      await work()
    } catch (e) {
      setError(errorMessage(e))
    } finally {
      setBusy(false)
    }
  }

  function recordResults(nodes: ApplyResult[]) {
    setApplyResult(nodes)
    setNotice(nodes.length ? 'Операции переданы в очередь Node. Ожидайте подтверждения применения через WSS.' : 'Изменения сохранены. Привязанных работающих Node не найдено.')
  }

  async function saveCertificate() {
    await run(async () => {
      if (!certificate.trim() || !privateKey.trim()) throw new Error('Укажите сертификат и закрытый ключ в формате PEM.')
      if (activeId) {
        const result = await $fetch<{ revision: number; agent_count: number }>(base + '/' + activeId, {
          method: 'PUT', body: { certificate, private_key: privateKey },
        })
        setApplyResult([])
        setNotice(result.agent_count ? `Обновление сертификата поставлено в очередь для ${result.agent_count} Node. TLS-handshake ещё не подтверждён.` : 'Сертификат сохранён; привязанных автономных Node нет.')
      } else {
        if (!name.trim()) throw new Error('Введите название пресета.')
        const created = await $fetch<Preset>(base, {
          method: 'POST', body: { name: name.trim(), certificate, private_key: privateKey },
        })
        selectPreset(created.id)
        setRenamedName(created.name)
        setNotice('Сертификат сохранён. Теперь выберите ноды и TLS-inbound.')
      }
      setCertificate('')
      setPrivateKey('')
      await load()
    })
  }

  async function renamePreset() {
    await run(async () => {
      if (!activeId || !renamedName.trim()) throw new Error('Введите новое название пресета.')
      await $fetch(base + '/' + activeId + '/name', {
        method: 'PATCH', body: { name: renamedName.trim() },
      })
      await load()
      setNotice('Название сертификата обновлено.')
    })
  }

  async function deletePreset() {
    if (!activeId || bindings.length) return
    await run(async () => {
      await $fetch(base + '/' + activeId, { method: 'DELETE' })
      selectPreset('')
      setRenamedName('')
      setCertificate('')
      setPrivateKey('')
      setWebhookToken('')
      await load()
      setNotice('TLS-пресет удалён.')
    })
  }

  async function bind() {
    await run(async () => {
      const target = targets.find(item => selectedTarget === item.node_uuid + '|' + item.inbound_tag)
      if (!target || !activeId) throw new Error('Выберите сертификат и автономную Node с TLS-inbound.')
      if (target.runtime_status !== 'running') throw new Error('Для привязки TLS необходимо работающее Xray-ядро.')
      await $fetch('/api/node-registry/nodes/' + encodeURIComponent(target.node_uuid) +
        '/xray/tls/' + encodeURIComponent(target.inbound_tag), {
        method: 'PUT', body: { preset_id: activeId, expected_generation: target.generation },
      })
      setApplyResult([{ node_uuid: target.node_uuid, status: 'pending' }])
      setNotice('Сертификат привязан к Node; отправка через WSS ожидает подтверждения.')
      await load()
    })
  }

  async function unbind(binding: Binding) {
    await run(async () => {
      const target = targets.find(item => item.node_uuid === binding.node_uuid && item.inbound_tag === binding.inbound_tag)
      if (!target) throw new Error('Node больше не доступна в реестре TLS. Обновите список.')
      await $fetch('/api/node-registry/nodes/' + encodeURIComponent(binding.node_uuid) +
        '/xray/tls/' + encodeURIComponent(binding.inbound_tag), {
        method: 'DELETE', query: { expected_generation: target.generation },
      })
      setApplyResult([{ node_uuid: binding.node_uuid, status: 'pending' }])
      setNotice('TLS-привязка удалена; изменение поставлено в очередь Node.')
      await load()
    })
  }

  async function applyAll() {
    await run(async () => {
      if (!activeId) return
      const result = await $fetch<{ nodes: ApplyResult[] }>(
        '/api/node-registry/tls-presets/' + encodeURIComponent(activeId) + '/apply', { method: 'POST' })
      recordResults(result.nodes)
      await load()
    })
  }

  async function updateViaWebhook() {
    await run(async () => {
      if (!webhookToken || !certificate.trim() || !privateKey.trim()) {
        throw new Error('Для проверки укажите новый PEM сертификат, закрытый ключ и выпустите сервисный токен.')
      }
      const response = await window.fetch(base + '/' + activeId + '/webhook', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Authorization: 'Bearer ' + webhookToken },
        body: JSON.stringify({ certificate, private_key: privateKey }),
      })
      if (!response.ok) throw new Error('Webhook отклонил обновление (HTTP ' + response.status + ')')
      const result = await response.json() as { revision: number; agent_count: number }
      setCertificate('')
      setPrivateKey('')
      setApplyResult([])
      setNotice(result.agent_count ? `Webhook принят; обновление отправлено в очередь для ${result.agent_count} Node.` : 'Webhook принят; подключённых автономных Node нет.')
      await load()
    })
  }

  async function generateToken() {
    await run(async () => {
      const result = await $fetch<{ token: string }>(base + '/' + activeId + '/webhook-token', { method: 'POST' })
      setWebhookToken(result.token)
      setNotice('Сервисный токен перевыпущен. Сохраните его: он отображается только сейчас.')
    })
  }

  return (
    <div className="mx-auto w-full max-w-5xl space-y-6 p-4 pb-12 md:p-8">
      <div>
        <h1 className="text-2xl font-semibold">TLS-сертификаты</h1>
        <p className="text-muted-foreground mt-1 text-sm">
          Централизованные пресеты X.509 для TLS-входящих ядра Xray. Закрытые ключи зашифрованы;
          на Node передаются только через защищённый WSS при применении конфигурации.
        </p>
      </div>

      {error && <div role="alert" className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-destructive p-3 text-sm text-destructive">
        <span>{error}</span>
        <Button type="button" size="sm" variant="outline" disabled={loading} onClick={() => void load().catch(() => {})}>Повторить загрузку</Button>
      </div>}
      {loading && !loaded && <p role="status" className="text-sm text-muted-foreground">Загрузка каталога TLS…</p>}
      {notice && <div role="status" className="rounded-md border p-3 text-sm">{notice}</div>}

      <Tabs value={tab} onValueChange={selectTab} className="space-y-4">
        <TabsList className="h-auto w-full flex-wrap justify-start gap-1 sm:w-fit" aria-label="Разделы TLS">
          <TabsTrigger value="catalog" className="min-h-10 flex-1 sm:flex-none">Каталог</TabsTrigger>
          <TabsTrigger value="acme" className="min-h-10 flex-1 sm:flex-none">ACME / DNS</TabsTrigger>
          <TabsTrigger value="bindings" className="min-h-10 flex-1 sm:flex-none">Привязки Node</TabsTrigger>
          <TabsTrigger value="webhook" className="min-h-10 flex-1 sm:flex-none">Webhook</TabsTrigger>
        </TabsList>
        <TabsContent value="catalog" className="space-y-4">
      <section className="bg-card space-y-4 rounded-xl border p-5">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <h2 className="font-semibold">Каталог пресетов</h2>
          <Button variant="outline" onClick={() => {
            selectPreset(''); setName(''); setRenamedName(''); setCertificate(''); setPrivateKey(''); setWebhookToken(''); setApplyResult([])
          }}>Новый сертификат</Button>
        </div>
        <TlsPresetPicker presets={presets} value={activeId} onChange={choosePreset} />
        {selected && (
          <div className="flex flex-wrap gap-2">
            <Input className="min-w-52 flex-1" value={renamedName} onChange={e => setRenamedName(e.target.value)} aria-label="Новое название пресета" />
            <Button variant="outline" disabled={busy || !renamedName.trim()} onClick={() => void renamePreset()}>Переименовать</Button>
            <AlertDialog>
              <AlertDialogTrigger asChild>
                <Button variant="destructive" disabled={busy || bindings.length > 0}>Удалить</Button>
              </AlertDialogTrigger>
              <AlertDialogContent>
                <AlertDialogHeader>
                  <AlertDialogTitle>Удалить TLS-сертификат?</AlertDialogTitle>
                  <AlertDialogDescription>Пресет будет удалён без возможности отмены. Привязанные сертификаты удалять нельзя.</AlertDialogDescription>
                </AlertDialogHeader>
                <AlertDialogFooter>
                  <AlertDialogCancel>Отмена</AlertDialogCancel>
                  <AlertDialogAction onClick={() => void deletePreset()}>Удалить пресет</AlertDialogAction>
                </AlertDialogFooter>
              </AlertDialogContent>
            </AlertDialog>
          </div>
        )}
        {selected && <div className="space-y-3 rounded-md border p-3 text-sm">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <p className="font-medium">{selected.name} · ревизия {selected.revision}</p>
            <Badge variant={expiry === 'expired' ? 'red'
              : expiry === 'soon' ? 'yellow' : 'outline'}>
              {expiry === 'expired' ? 'Срок истёк'
                : expiry === 'soon' ? 'Срок менее 30 дней'
                  : expiry === 'valid' ? 'Срок более 30 дней' : 'Срок неизвестен'}
            </Badge>
          </div>
          <p><span className="text-muted-foreground">Истекает:</span> {Number.isFinite(Date.parse(selected.not_after)) ? new Date(selected.not_after).toLocaleString() : 'неизвестно'}</p>
          <p className="break-words"><span className="text-muted-foreground">Subject:</span> {selected.subject}</p>
          <details className="text-xs text-muted-foreground">
            <summary className="cursor-pointer">Идентификатор и отпечаток сертификата</summary>
            <p className="mt-2 break-all font-mono">ID: {selected.id}</p>
            <p className="mt-1 break-all font-mono">SHA-256: {selected.fingerprint_sha256}</p>
          </details>
          <p className="text-xs text-muted-foreground">Оценка учитывает только срок действия, не доверие цепочке и не доставку сертификата на Node.</p>
        </div>}
      </section>

      <section className="bg-card space-y-4 rounded-xl border p-5">
        <h2 className="font-semibold">{activeId ? 'Обновить сертификат' : 'Новый сертификат'}</h2>
        {!activeId && <label className="block space-y-2 text-sm">
          <span>Название</span>
          <Input value={name} onChange={e => setName(e.target.value)} placeholder="Например, edge.example.com / wildcard" />
        </label>}
        <div className="grid gap-4 lg:grid-cols-2">
          <label className="space-y-2 text-sm">
            <span>Публичный сертификат (PEM)</span>
            <Textarea className="min-h-48 font-mono text-xs" value={certificate}
              onChange={e => setCertificate(e.target.value)} placeholder="-----BEGIN CERTIFICATE-----" />
          </label>
          <label className="space-y-2 text-sm">
            <span>Закрытый ключ (PEM)</span>
            <Textarea className="min-h-48 font-mono text-xs" value={privateKey}
              onChange={e => setPrivateKey(e.target.value)} placeholder="-----BEGIN PRIVATE KEY-----" />
          </label>
        </div>
        <p className="text-muted-foreground text-xs">Сертификат и ключ должны соответствовать друг другу и быть действительными. Закрытый ключ никогда не возвращается через API.</p>
        {activeId ? <AlertDialog>
          <AlertDialogTrigger asChild>
            <Button disabled={busy || !certificate.trim() || !privateKey.trim()}>Заменить PEM и создать ревизию</Button>
          </AlertDialogTrigger>
          <AlertDialogContent>
            <AlertDialogHeader>
              <AlertDialogTitle>Заменить сертификат и приватный ключ?</AlertDialogTitle>
              <AlertDialogDescription>Новая ревизия будет сохранена в Backend. Для привязанных Node обновление Xray будет поставлено в очередь, а TLS-handshake ещё не подтверждён. Проверьте клиентские подключения после применения.</AlertDialogDescription>
            </AlertDialogHeader>
            <AlertDialogFooter>
              <AlertDialogCancel>Отмена</AlertDialogCancel>
              <AlertDialogAction onClick={() => void saveCertificate()}>Создать новую ревизию</AlertDialogAction>
            </AlertDialogFooter>
          </AlertDialogContent>
        </AlertDialog> : <Button disabled={busy || !name.trim() || !certificate.trim() || !privateKey.trim()}
          onClick={() => void saveCertificate()}>Сохранить сертификат</Button>}
      </section>
        </TabsContent>
        <TabsContent value="acme" forceMount={acmeOpened ? true : undefined} className="space-y-4">
          <ACMEPanel />
        </TabsContent>
        <TabsContent value="bindings" className="space-y-4">
          <TlsPresetPicker presets={presets} value={activeId} onChange={choosePreset} />
          {!activeId && <div className="rounded-xl border border-dashed p-6 text-sm text-muted-foreground">
            Выберите TLS-сертификат в <button type="button" className="text-primary underline" onClick={() => selectTab('catalog')}>каталоге</button>, затем назначьте его на Node.
          </div>}
          {activeId && <>
          <section className="bg-card space-y-4 rounded-xl border p-5">
            <h2 className="font-semibold">Назначение на ноды</h2>
            <p className="text-muted-foreground text-sm">Выберите конкретный TLS-входящий интерфейс. Изменения применяются только к выбранным нодам.</p>
            <div className="flex flex-col gap-3 sm:flex-row">
              <select className="bg-background min-w-0 flex-1 rounded-md border p-2 text-sm" value={selectedTarget} onChange={e => setSelectedTarget(e.target.value)}>
                <option value="">Выберите ноду и TLS-inbound</option>
                {targets.map(t => <option key={t.node_uuid + ':' + t.inbound_tag} value={t.node_uuid + '|' + t.inbound_tag}>
                  {t.node_name} · {t.inbound_tag} · {t.core_id == null ? 'Node Core' : 'Core #' + t.core_id} · {t.runtime_status}
                </option>)}
              </select>
              <Button disabled={busy || !selectedTarget} onClick={() => void bind()}>Подключить</Button>
            </div>
            {!targets.length && <p className="text-muted-foreground text-sm">У нод пока нет TLS-inbound. Добавьте TLS в редакторе <Link className="underline" to="/nodes/cores">ядра</Link>.</p>}
            <div className="space-y-2">
              {bindings.map(b => (
                <div key={b.node_uuid + ':' + b.inbound_tag} className="flex items-center justify-between gap-3 rounded-md border p-3 text-sm">
                  <div className="min-w-0 flex-1 space-y-1">
                    <div>{targets.find(t => t.node_uuid === b.node_uuid)?.node_name ?? b.node_uuid} · {b.inbound_tag}</div>
                    <div className="text-muted-foreground text-xs">{b.preset_name} · ревизия {b.revision} · доставка TLS не проверена</div>
                  </div>
                  <div className="flex flex-wrap gap-2">
                    <Link className="text-sm underline" to={`/nodes/registry/${encodeURIComponent(b.node_uuid)}?tab=tls`}>TLS на Node</Link>
                    <AlertDialog>
                      <AlertDialogTrigger asChild>
                        <Button variant="outline" size="sm" disabled={busy}>Отключить</Button>
                      </AlertDialogTrigger>
                      <AlertDialogContent>
                        <AlertDialogHeader>
                          <AlertDialogTitle>Снять TLS-привязку?</AlertDialogTitle>
                          <AlertDialogDescription>Сертификат будет отвязан от inbound {b.inbound_tag}. Изменение попадёт в очередь Node и может повлиять на VPN-подключения.</AlertDialogDescription>
                        </AlertDialogHeader>
                        <AlertDialogFooter>
                          <AlertDialogCancel>Отмена</AlertDialogCancel>
                          <AlertDialogAction onClick={() => void unbind(b)}>Снять привязку</AlertDialogAction>
                        </AlertDialogFooter>
                      </AlertDialogContent>
                    </AlertDialog>
                  </div>
                </div>
              ))}
            </div>
            <AlertDialog>
              <AlertDialogTrigger asChild>
                <Button variant="outline" disabled={busy || !bindings.length}>Повторно применить на всех привязанных Node</Button>
              </AlertDialogTrigger>
              <AlertDialogContent>
                <AlertDialogHeader>
                  <AlertDialogTitle>Поставить TLS-обновление в очередь всех привязанных Node?</AlertDialogTitle>
                  <AlertDialogDescription>Изменение затронет {bindings.length} привязок. Результат применения и TLS-handshake потребуется проверить отдельно.</AlertDialogDescription>
                </AlertDialogHeader>
                <AlertDialogFooter>
                  <AlertDialogCancel>Отмена</AlertDialogCancel>
                  <AlertDialogAction onClick={() => void applyAll()}>Поставить в очередь</AlertDialogAction>
                </AlertDialogFooter>
              </AlertDialogContent>
            </AlertDialog>
            {!!applyResult.length && (
              <div aria-label="Результаты применения" className="space-y-2 text-sm">
                {applyResult.map(r => <div key={r.node_uuid} className="rounded-md border p-2">
                  {targets.find(item => item.node_uuid === r.node_uuid)?.node_name ?? r.node_uuid}:
                  {' '}{r.status === 'pending' ? 'ожидает подтверждения Node' : r.status}
                </div>)}
              </div>
            )}
          </section>
          </>}
        </TabsContent>
        <TabsContent value="webhook" className="space-y-4">
          <TlsPresetPicker presets={presets} value={activeId} onChange={choosePreset} />
          {!activeId && <div className="rounded-xl border border-dashed p-6 text-sm text-muted-foreground">
            Для сервисного обновления сначала выберите сертификат в <button type="button" className="text-primary underline" onClick={() => selectTab('catalog')}>каталоге</button>.
          </div>}
          {activeId && <>
          <section className="bg-card space-y-3 rounded-xl border p-5">
            <h2 className="font-semibold">Сервисное обновление / Webhook</h2>
            <p className="text-muted-foreground text-sm">
              Отдельный Bearer-токен даёт только право заменить сертификат этого пресета.
              Отправляйте POST в <code className="break-all">/api/tls-presets/{activeId}/webhook</code> с JSON
              <code> certificate</code> и <code>private_key</code>. После обновления привязанные Node получат новую ревизию desired-state по WSS; это не подтверждение TLS-handshake.
            </p>
            <AlertDialog>
              <AlertDialogTrigger asChild>
                <Button variant="outline" disabled={busy}>Перевыпустить сервисный токен</Button>
              </AlertDialogTrigger>
              <AlertDialogContent>
                <AlertDialogHeader>
                  <AlertDialogTitle>Перевыпустить токен этого сертификата?</AlertDialogTitle>
                  <AlertDialogDescription>Предыдущий сервисный токен перестанет работать. Новый будет отображён однократно — заранее подготовьте защищённое хранилище.</AlertDialogDescription>
                </AlertDialogHeader>
                <AlertDialogFooter>
                  <AlertDialogCancel>Отмена</AlertDialogCancel>
                  <AlertDialogAction onClick={() => void generateToken()}>Перевыпустить токен</AlertDialogAction>
                </AlertDialogFooter>
              </AlertDialogContent>
            </AlertDialog>
            {webhookToken && (
              <div className="space-y-2">
                <p className="text-xs">Сохраните секрет в защищённом хранилище. После ухода со страницы он не отображается.</p>
                <Input readOnly type="password" aria-label="Одноразовый сервисный токен" value={webhookToken} />
                <Button size="sm" variant="outline" onClick={() => void navigator.clipboard.writeText(webhookToken)}>Копировать токен</Button>
                <Button size="sm" variant="outline" disabled={busy || !certificate.trim() || !privateKey.trim()}
                  onClick={() => void updateViaWebhook()}>Применить PEM через сервисный webhook</Button>
              </div>
            )}
            <p className="text-muted-foreground text-xs">Выпуск и продление через ACME доступны во вкладке «ACME / DNS». Сервисный webhook предназначен только для обновления выбранного пресета.</p>
          </section>
          </>}
        </TabsContent>
      </Tabs>
    </div>
  )
}
