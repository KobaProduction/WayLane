import { useCallback, useEffect, useState } from 'react'
import { toast } from 'sonner'
import { $fetch } from '@/service/http'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'
import { Switch } from '@/components/ui/switch'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'

type Template = { title: string; lines: string[] }
type Editor = {
  enabled: boolean
  scenarios: Record<string, Template>
  webhook_enabled: boolean
  webhook_url: string | null
  device_check_url: string | null
}
type Event = {
  id: string
  event_type: string
  status: string
  attempts: number
  last_error: string | null
  next_attempt_at: string | null
  created_at: string
  delivered_at: string | null
}
const scenarios: Record<string, string> = {
  invalid_link: 'Ссылка на подписку недействительна',
  device_limit: 'Превышен лимит устройств',
  hwid_required: 'HWID отсутствует',
  unsupported_client: 'Приложение не поддерживается',
  rate_limited: 'Слишком частое обновление',
  access_denied: 'Доступ запрещён',
  bot_unavailable: 'Ошибка проверки устройства',
}
const base = '/api/subscription-firewall'

function message(error: unknown): string {
  return error instanceof Error ? error.message : 'Неизвестная ошибка'
}

export function SubscriptionFirewallDesigner() {
  const [config, setConfig] = useState<Editor | null>(null)
  const [active, setActive] = useState('invalid_link')
  const [events, setEvents] = useState<Event[]>([])
  const [eventSummary, setEventSummary] = useState<Record<string, number>>({})
  const [preview, setPreview] = useState<{ base64_payload: string; announce: string } | null>(null)
  const [busy, setBusy] = useState(false)
  const [loadError, setLoadError] = useState('')
  const refresh = useCallback(async () => {
    try {
      const [settings, rows, summary] = await Promise.all([
        $fetch<Editor>(base),
        $fetch<Event[]>(base + '/device-events'),
        $fetch<Record<string, number>>(base + '/device-events/summary'),
      ])
      setConfig(settings)
      setEvents(rows)
      setEventSummary(summary)
      setLoadError('')
    } catch (error) {
      setLoadError(message(error))
      throw error
    }
  }, [])
  useEffect(() => { void refresh().catch(() => {}) }, [refresh])

  function updateTemplate(field: 'title' | 'lines', value: string) {
    setConfig(current => current ? {
      ...current,
      scenarios: {
        ...current.scenarios,
        [active]: {
          ...current.scenarios[active],
          [field]: field === 'lines' ? value.split('\n') : value,
        },
      },
    } : current)
    setPreview(null)
  }
  async function act(task: () => Promise<void>) {
    setBusy(true)
    try { await task() }
    catch (error) { toast.error(message(error)) }
    finally { setBusy(false) }
  }
  function save() {
    if (!config) return
    void act(async () => {
      await $fetch(base, { method: 'PUT', body: config })
      toast.success('Настройки сохранены')
      await refresh()
    })
  }
  function showPreview() {
    if (!config) return
    void act(async () => {
      setPreview(await $fetch(base + '/preview/' + active, {
        method: 'POST',
        body: config.scenarios[active],
      }))
    })
  }
  function retry() {
    void act(async () => {
      const data = await $fetch<{ selected: number; delivered: number }>(base + '/retry-device-events', {
        method: 'POST',
      })
      toast.success('Доставлено ' + data.delivered + ' из ' + data.selected)
      await refresh()
    })
  }

  function requeue(event: Event) {
    if (!window.confirm('Заново запустить отправку события ' + event.id + '?')) return
    void act(async () => {
      await $fetch(base + '/device-events/' + event.id + '/requeue', { method: 'POST' })
      toast.success('Событие возвращено в очередь; следующая отправка по расписанию')
      await refresh()
    })
  }

  if (!config) return (
    <div className="space-y-3 p-4" aria-live="polite">
      {loadError ? (
        <div role="alert" className="space-y-2 rounded-md border border-destructive/40 p-4 text-sm">
          <p>Не удалось загрузить Subscription Firewall: {loadError}</p>
          <Button type="button" variant="outline" onClick={() => void refresh().catch(() => {})}>Повторить загрузку</Button>
        </div>
      ) : <p className="text-sm text-muted-foreground">Загрузка Subscription Firewall…</p>}
    </div>
  )
  const template = config.scenarios[active]

  return <div className="space-y-4 p-4">
    {loadError && <div role="alert" className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-destructive/40 p-3 text-sm">
      <span>Не удалось обновить данные: {loadError}</span>
      <Button type="button" variant="outline" size="sm" onClick={() => void refresh().catch(() => {})}>Повторить</Button>
    </div>}
    <Tabs defaultValue="messages" className="w-full">
      <TabsList className="w-full justify-start">
        <TabsTrigger value="messages">Клиентские сообщения</TabsTrigger>
        <TabsTrigger value="events">Вебхуки и события</TabsTrigger>
      </TabsList>
      <TabsContent value="messages" className="mt-4">
    <Card>
      <CardHeader>
        <CardTitle>Шаблоны ответов подписки</CardTitle>
        <p className="text-sm text-muted-foreground">Настройка сообщений для поддерживаемых клиентов при ошибках доступа.</p>
        <details className="text-muted-foreground text-xs"><summary className="cursor-pointer">О формате ответов</summary>
          Base64 и заголовок announce не применяются к HTML и запросам без HWID. Проверка доступа остаётся обязательной.
        </details>
      </CardHeader>
      <CardContent className="space-y-3">
        <label className="flex items-center gap-2 text-sm">
          <Switch checked={config.enabled} onCheckedChange={enabled => setConfig({ ...config, enabled })} />
          Заменять ошибки на понятные сообщения (HTTP 200 для поддерживаемых клиентов)
        </label>
        <label className="block text-sm">
          Сценарий
          <select className="bg-background mt-1 h-10 w-full rounded-md border px-3" value={active}
            onChange={event => { setActive(event.target.value); setPreview(null) }}>
            {Object.entries(scenarios).map(([key, name]) => <option key={key} value={key}>{name}</option>)}
          </select>
        </label>
        <label className="block text-sm">
          Заголовок сообщения
          <Input value={template.title} maxLength={256} onChange={event => updateTemplate('title', event.target.value)} />
        </label>
        <label className="block text-sm">
          Информационные строки (1–8, каждая до 256 символов)
          <Textarea rows={4} value={template.lines.join('\n')} onChange={event => updateTemplate('lines', event.target.value)} />
        </label>
        <div className="flex flex-wrap gap-2">
          <Button variant="outline" disabled={busy} onClick={showPreview}>Предпросмотр ответа</Button>
          <Button disabled={busy} onClick={save}>Сохранить шаблоны</Button>
        </div>
        {preview && <div className="rounded-md border p-3 text-sm">
          <p className="font-medium">HTTP 200 · Base64 · announce</p>
          <p className="whitespace-pre-wrap">{template.lines.join('\n')}</p>
          <details className="mt-2"><summary>Технические данные ответа</summary>
            <code className="block break-all text-xs">{preview.announce}</code>
            <code className="block break-all text-xs">{preview.base64_payload}</code>
          </details>
        </div>}
      </CardContent>
    </Card>
      </TabsContent>
      <TabsContent value="events" className="mt-4">
    <Card>
      <CardHeader>
        <CardTitle>Контроль устройств и вебхуки</CardTitle>
        <p className="text-sm text-muted-foreground">Состояние доставки событий устройств и настройки внешнего webhook.</p>
        <details className="text-muted-foreground text-xs"><summary className="cursor-pointer">Защита и повторные отправки</summary>
          События HWID подписываются HMAC SHA-256; секрет задаётся в Backend через WAYLANE_DEVICE_WEBHOOK_SECRET.
          Недоставленные события сохраняются в БД, повторы выполняются до 10 попыток с увеличением задержки.
        </details>
      </CardHeader>
      <CardContent className="space-y-3">
        <label className="flex items-center gap-2 text-sm">
          <Switch checked={config.webhook_enabled}
            onCheckedChange={webhook_enabled => setConfig({ ...config, webhook_enabled })} />
          Отправлять события об устройствах
        </label>
        <label className="block text-sm">
          URL обработчика событий (HTTPS)
          <Input value={config.webhook_url ?? ''} placeholder="https://example.com/device-event"
            onChange={event => setConfig({ ...config, webhook_url: event.target.value || null })} />
        </label>
        <label className="block text-sm">
          URL проверки устройств legacy /webhook/check_device (необязательно)
          <Input value={config.device_check_url ?? ''} placeholder="https://example.com/webhook/check_device"
            onChange={event => setConfig({ ...config, device_check_url: event.target.value || null })} />
        </label>
        <div className="flex flex-wrap gap-2">
          <Button disabled={busy} onClick={save}>Сохранить webhook</Button>
          <Button variant="outline" disabled={busy} onClick={retry}>Повторить недоставленные</Button>
          <Button variant="outline" disabled={busy} onClick={() => void act(refresh)}>Обновить список</Button>
        </div>
        <div className="flex flex-wrap gap-x-4 gap-y-1 text-sm text-muted-foreground">
          <span>Ожидают: {eventSummary.pending ?? 0}</span>
          <span>В отправке: {eventSummary.sending ?? 0}</span>
          <span>Повтор: {eventSummary.failed ?? 0}</span>
          <span>Доставлены: {eventSummary.delivered ?? 0}</span>
          <span className={(eventSummary.exhausted ?? 0) > 0 ? 'font-medium text-destructive' : ''}>
            Исчерпали попытки: {eventSummary.exhausted ?? 0}
          </span>
        </div>
        <div className="max-h-60 space-y-2 overflow-auto text-xs">
          {!events.length && <p className="text-muted-foreground">Событий пока нет</p>}
          {events.map(event => <div key={event.id} className="rounded-md border p-2">
            <p>
              {event.event_type} · <strong>{event.status}</strong> · попытки: {event.attempts}/10
              {event.last_error && ' · причина: ' + event.last_error}
            </p>
            <p className="mt-1 text-muted-foreground">
              {event.next_attempt_at
                ? 'Следующая попытка: ' + new Date(event.next_attempt_at).toLocaleString()
                : event.status === 'exhausted'
                  ? 'Автоматические попытки остановлены — требуется вмешательство'
                  : event.status === 'delivered' ? 'Доставлено' : 'Ожидает обработки'}
            </p>
            {event.status === 'exhausted' && (
              <Button className="mt-2" size="sm" variant="outline" disabled={busy} onClick={() => requeue(event)}>
                Вернуть в очередь
              </Button>
            )}
          </div>)}
        </div>
      </CardContent>
    </Card>
      </TabsContent>
    </Tabs>
  </div>
}
