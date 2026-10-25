import { ACMEPanel } from '@/features/tls/acme-panel'
import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router'
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
type Target = { node_id: number; node_name: string; core_id: number; inbound_tag: string }
type Binding = { id: string; preset_id: string; node_id: number; core_id: number; inbound_tag: string }
type ApplyResult = { node_id: number; status: string; detail?: string }
type TLSProbe = { reachable: boolean; fingerprint_match: boolean; presented_fingerprint_sha256?: string }

const base = '/api/tls-presets'
const errorMessage = (reason: unknown) =>
  reason instanceof Error ? reason.message : 'Неизвестная ошибка сервера'

export default function TlsPresetsPage() {
  const [presets, setPresets] = useState<Preset[]>([])
  const [targets, setTargets] = useState<Target[]>([])
  const [bindings, setBindings] = useState<Binding[]>([])
  const [activeId, setActiveId] = useState('')
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
  const [probes, setProbes] = useState<Record<string, TLSProbe>>({})

  const selected = presets.find(p => p.id === activeId)
  const load = useCallback(async () => {
    const [items, available] = await Promise.all([
      $fetch<Preset[]>(base),
      $fetch<Target[]>(base + '/targets'),
    ])
    setPresets(items)
    setTargets(available)
    if (activeId && !items.some(p => p.id === activeId)) setActiveId('')
  }, [activeId])

  useEffect(() => {
    void load().catch(e => setError(errorMessage(e)))
  }, [load])

  useEffect(() => {
    if (!activeId) {
      setBindings([])
      return
    }
    void $fetch<Binding[]>(base + '/' + activeId + '/bindings')
      .then(setBindings)
      .catch(e => setError(errorMessage(e)))
  }, [activeId])

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
    setNotice(nodes.length ? 'Обновление конфигураций нод завершено. Проверьте статусы ниже.' : 'Изменения сохранены.')
  }

  async function saveCertificate() {
    await run(async () => {
      if (!certificate.trim() || !privateKey.trim()) throw new Error('Укажите сертификат и закрытый ключ в формате PEM.')
      if (activeId) {
        const result = await $fetch<{ revision: number; nodes: ApplyResult[] }>(base + '/' + activeId, {
          method: 'PUT', body: { certificate, private_key: privateKey },
        })
        recordResults(result.nodes)
      } else {
        if (!name.trim()) throw new Error('Введите название пресета.')
        const created = await $fetch<Preset>(base, {
          method: 'POST', body: { name: name.trim(), certificate, private_key: privateKey },
        })
        setActiveId(created.id)
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
    if (!window.confirm('Удалить TLS-пресет? Отменить это действие нельзя.')) return
    await run(async () => {
      await $fetch(base + '/' + activeId, { method: 'DELETE' })
      setActiveId('')
      setRenamedName('')
      setCertificate('')
      setPrivateKey('')
      setWebhookToken('')
      setBindings([])
      await load()
      setNotice('TLS-пресет удалён.')
    })
  }

  async function bind() {
    await run(async () => {
      const target = targets.find(t => selectedTarget === t.node_id + '|' + t.inbound_tag)
      if (!target || !activeId) throw new Error('Выберите сертификат и ноду с TLS-inbound.')
      const result = await $fetch<{ apply: ApplyResult[] }>(base + '/' + activeId + '/bindings', {
        method: 'POST', body: { node_id: target.node_id, inbound_tag: target.inbound_tag },
      })
      recordResults(result.apply)
      setBindings(await $fetch<Binding[]>(base + '/' + activeId + '/bindings'))
    })
  }

  async function unbind(bindingId: string) {
    await run(async () => {
      const result = await $fetch<{ apply: ApplyResult[] }>(base + '/' + activeId + '/bindings/' + bindingId, { method: 'DELETE' })
      recordResults(result.apply)
      setBindings(await $fetch<Binding[]>(base + '/' + activeId + '/bindings'))
    })
  }

  async function verifyBinding(bindingId: string) {
    await run(async () => {
      const probe = await $fetch<TLSProbe>(base + '/' + activeId + '/bindings/' + bindingId + '/check')
      setProbes(previous => ({ ...previous, [bindingId]: probe }))
      setNotice(probe.reachable && probe.fingerprint_match
        ? 'TLS-handshake пройден: установленный сертификат совпадает с пресетом.'
        : 'TLS-handshake или совпадение отпечатка не подтверждены.')
    })
  }

  async function applyAll() {
    await run(async () => {
      const result = await $fetch<{ nodes: ApplyResult[] }>(base + '/' + activeId + '/apply', { method: 'POST' })
      recordResults(result.nodes)
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
      const result = await response.json() as { revision: number; nodes: ApplyResult[] }
      setCertificate('')
      setPrivateKey('')
      recordResults(result.nodes)
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
          на ноды передаются только при применении конфигурации.
        </p>
      </div>

      {error && <div role="alert" className="rounded-md border border-destructive p-3 text-sm text-destructive">{error}</div>}
      {notice && <div role="status" className="rounded-md border p-3 text-sm">{notice}</div>}

      <section className="bg-card space-y-4 rounded-xl border p-5">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <h2 className="font-semibold">Каталог пресетов</h2>
          <Button variant="outline" onClick={() => {
            setActiveId(''); setName(''); setRenamedName(''); setCertificate(''); setPrivateKey(''); setWebhookToken(''); setApplyResult([])
          }}>Новый сертификат</Button>
        </div>
        <label className="block space-y-2 text-sm">
          <span>Выбрать существующий</span>
          <select
            className="bg-background w-full rounded-md border px-3 py-2"
            value={activeId}
            onChange={e => {
              setActiveId(e.target.value)
              setRenamedName(presets.find(p => p.id === e.target.value)?.name ?? '')
              setCertificate(''); setPrivateKey(''); setWebhookToken(''); setApplyResult([])
            }}
          >
            <option value="">Создать новый</option>
            {presets.map(p => <option key={p.id} value={p.id}>{p.name} · v{p.revision}</option>)}
          </select>
        </label>
        {selected && (
          <div className="flex flex-wrap gap-2">
            <Input className="min-w-52 flex-1" value={renamedName} onChange={e => setRenamedName(e.target.value)} aria-label="Новое название пресета" />
            <Button variant="outline" disabled={busy || !renamedName.trim()} onClick={() => void renamePreset()}>Переименовать</Button>
            <Button variant="outline" disabled={busy || bindings.length > 0} onClick={() => void deletePreset()}>Удалить</Button>
          </div>
        )}
        {selected && (
          <div className="grid gap-2 text-sm md:grid-cols-2">
            <div><span className="text-muted-foreground">ID:</span> <code className="break-all">{selected.id}</code></div>
            <div><span className="text-muted-foreground">Истекает:</span> {new Date(selected.not_after).toLocaleString()}</div>
            <div className="md:col-span-2"><span className="text-muted-foreground">Subject:</span> {selected.subject}</div>
            <div className="md:col-span-2"><span className="text-muted-foreground">SHA-256:</span> <code className="break-all">{selected.fingerprint_sha256}</code></div>
          </div>
        )}
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
        <Button disabled={busy} onClick={() => void saveCertificate()}>
          {activeId ? 'Обновить и применить к нодам' : 'Сохранить пресет'}
        </Button>
      </section>

      <ACMEPanel />

      {activeId && (
        <>
          <section className="bg-card space-y-4 rounded-xl border p-5">
            <h2 className="font-semibold">Назначение на ноды</h2>
            <p className="text-muted-foreground text-sm">Выберите конкретный TLS-входящий интерфейс. Изменения применяются только к выбранным нодам.</p>
            <div className="flex flex-col gap-3 sm:flex-row">
              <select className="bg-background min-w-0 flex-1 rounded-md border p-2 text-sm" value={selectedTarget} onChange={e => setSelectedTarget(e.target.value)}>
                <option value="">Выберите ноду и TLS-inbound</option>
                {targets.map(t => <option key={t.node_id + ':' + t.inbound_tag} value={t.node_id + '|' + t.inbound_tag}>
                  {t.node_name} · {t.inbound_tag} · Core #{t.core_id}
                </option>)}
              </select>
              <Button disabled={busy || !selectedTarget} onClick={() => void bind()}>Подключить</Button>
            </div>
            {!targets.length && <p className="text-muted-foreground text-sm">У нод пока нет TLS-inbound. Добавьте TLS в редакторе <Link className="underline" to="/nodes/cores">ядра</Link>.</p>}
            <div className="space-y-2">
              {bindings.map(b => (
                <div key={b.id} className="flex items-center justify-between gap-3 rounded-md border p-3 text-sm">
                  <div className="min-w-0 flex-1 space-y-1">
                    <div>Node #{b.node_id} · {b.inbound_tag} · Core #{b.core_id}</div>
                    {probes[b.id] && <p className="text-muted-foreground text-xs">
                      TLS: {probes[b.id].reachable ? 'доступен' : 'недоступен'},
                      отпечаток: {probes[b.id].fingerprint_match ? 'совпадает' : 'не совпадает'}
                    </p>}
                  </div>
                  <div className="flex flex-wrap gap-2">
                    <Button variant="outline" size="sm" disabled={busy} onClick={() => void verifyBinding(b.id)}>Проверить TLS</Button>
                    <Button variant="outline" size="sm" disabled={busy} onClick={() => void unbind(b.id)}>Отключить</Button>
                  </div>
                </div>
              ))}
            </div>
            <Button variant="outline" disabled={busy || !bindings.length} onClick={() => void applyAll()}>Проверить и применить ко всем привязанным нодам</Button>
            {!!applyResult.length && (
              <div aria-label="Результаты применения" className="space-y-2 text-sm">
                {applyResult.map(r => <div key={r.node_id} className="rounded-md border p-2">Node #{r.node_id}: {r.status}{r.detail ? ' — ' + r.detail : ''}</div>)}
              </div>
            )}
          </section>

          <section className="bg-card space-y-3 rounded-xl border p-5">
            <h2 className="font-semibold">Сервисное обновление / Webhook</h2>
            <p className="text-muted-foreground text-sm">
              Отдельный Bearer-токен даёт только право заменить сертификат этого пресета.
              Отправляйте POST в <code className="break-all">/api/tls-presets/{activeId}/webhook</code> с JSON
              <code> certificate</code> и <code>private_key</code>. После обновления привязанные ноды будут переподключены.
            </p>
            <Button variant="outline" disabled={busy} onClick={() => void generateToken()}>Перевыпустить сервисный токен</Button>
            {webhookToken && (
              <div className="space-y-2">
                <p className="text-xs">Сохраните секрет в защищённом хранилище. После ухода со страницы он не отображается.</p>
                <Input readOnly type="password" aria-label="Одноразовый сервисный токен" value={webhookToken} />
                <Button size="sm" variant="outline" onClick={() => void navigator.clipboard.writeText(webhookToken)}>Копировать токен</Button>
                <Button size="sm" variant="outline" disabled={busy || !certificate.trim() || !privateKey.trim()}
                  onClick={() => void updateViaWebhook()}>Применить PEM через сервисный webhook</Button>
              </div>
            )}
            <p className="text-muted-foreground text-xs">Автоматический выпуск через ACME/Let's Encrypt и интеграция с ACM — следующий этап.</p>
          </section>
        </>
      )}
    </div>
  )
}
