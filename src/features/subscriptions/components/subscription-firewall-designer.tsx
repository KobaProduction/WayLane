import { useCallback, useEffect, useState } from 'react'
import { toast } from 'sonner'
import { $fetch } from '@/service/http'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'
import { Switch } from '@/components/ui/switch'

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
  const refresh = useCallback(async () => {
    const [settings, rows, summary] = await Promise.all([
      $fetch<Editor>(base),
      $fetch<Event[]>(base + '/device-events'),
      $fetch<Record<string, number>>(base + '/device-events/summary'),
    ])
    setConfig(settings)
    setEvents(rows)
    setEventSummary(summary)
  }, [])
  useEffect(() => { void refresh().catch(error => toast.error(message(error))) }, [refresh])

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

  if (!config) return <p className="p-4 text-sm text-muted-foreground">Загрузка дизайнера…</p>
  const template = config.scenarios[active]

  return <div className="space-y-5 p-4">
    <Card>
      <CardHeader>
        <CardTitle>Subscription Firewall — дизайнер клиентских сообщений</CardTitle>
        <p className="text-sm text-muted-foreground">
          Legacy-формат для клиентских подписок: Base64 с информационными строками и заголовком announce.
          Не применяется к HTML и запросам без HWID; проверка реального доступа остаётся обязательной.
        </p>
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
    <Card>
      <CardHeader>
        <CardTitle>Контроль устройств и webhook</CardTitle>
        <p className="text-sm text-muted-foreground">
          События создания, изменения, удаления и сброса HWID подписываются HMAC SHA-256.
          Секрет задаётся только в backend: WAYLANE_DEVICE_WEBHOOK_SECRET.
          Недоставленные события сохраняются в БД и автоматически повторяются каждые 30 секунд,
          когда наступает время следующей попытки (exponential backoff, максимум 10 попыток).
          При необходимости можно подключить старый проверяющий Telegram-бота.
        </p>
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
  </div>
}
