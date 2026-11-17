import { useCallback, useEffect, useRef, useState } from 'react'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import {
  AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent,
  AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle,
  AlertDialogTrigger,
} from '@/components/ui/alert-dialog'
import { $fetch } from '@/service/http'
import { deviceEventLabel, statusLabel } from '../firewall-workspace-contract'
import type { DeviceEvent, DeviceEventSummary } from '../firewall-workspace-contract'

const base = '/api/subscription-firewall'
const statuses = ['pending', 'sending', 'failed', 'delivered', 'exhausted'] as const
const labels = { pending: 'В очереди', sending: 'В обработке', failed: 'Ошибки / повторы', delivered: 'Подтверждены', exhausted: 'Исчерпаны' }

type Props = { webhookEnabled: boolean; canUpdate: boolean }

/** One-shot operator reads. Summary spans all events; list contains at most the 100 latest records. */
export function SubscriptionFirewallEvents({ webhookEnabled, canUpdate }: Props) {
  const [events, setEvents] = useState<DeviceEvent[] | null>(null)
  const [summary, setSummary] = useState<DeviceEventSummary | null>(null)
  const [eventsReadAt, setEventsReadAt] = useState<Date | null>(null)
  const [summaryReadAt, setSummaryReadAt] = useState<Date | null>(null)
  const [filter, setFilter] = useState('all')
  const [errors, setErrors] = useState<string[]>([])
  const [loading, setLoading] = useState(false)
  const [acting, setActing] = useState(false)
  const [result, setResult] = useState<string | null>(null)
  const generation = useRef(0)

  const refresh = useCallback(async () => {
    const request = ++generation.current
    setLoading(true)
    const outcomes = await Promise.allSettled([
      $fetch<DeviceEvent[]>(base + '/device-events'),
      $fetch<DeviceEventSummary>(base + '/device-events/summary'),
    ])
    if (request !== generation.current) return
    const [rows, totals] = outcomes
    const problems: string[] = []
    if (rows.status === 'fulfilled') {
      setEvents(rows.value)
      setEventsReadAt(new Date())
    } else problems.push('Не удалось обновить журнал последних событий.')
    if (totals.status === 'fulfilled') {
      setSummary(totals.value)
      setSummaryReadAt(new Date())
    } else problems.push('Не удалось обновить общие счётчики доставки.')
    setErrors(problems)
    setLoading(false)
  }, [])

  useEffect(() => {
    void refresh()
    return () => { generation.current += 1 }
  }, [refresh])

  async function retryPending() {
    if (acting || !canUpdate) return
    setActing(true)
    try {
      const outcome = await $fetch<{ selected: number; delivered: number; failed?: number }>(base + '/retry-device-events', { method: 'POST' })
      const detail = `Проверено событий: ${outcome.selected}. Подтверждена доставка: ${outcome.delivered}. Не доставлено: ${outcome.failed ?? outcome.selected - outcome.delivered}.`
      setResult(detail)
      toast.success('Ручная попытка доставки завершена; результат ниже.')
      await refresh()
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Повторная отправка не выполнена')
    } finally { setActing(false) }
  }

  async function requeueExhausted(event: DeviceEvent) {
    if (acting || !canUpdate || event.status !== 'exhausted') return
    setActing(true)
    try {
      await $fetch(base + '/device-events/' + encodeURIComponent(event.id) + '/requeue', { method: 'POST' })
      toast.success('Событие возвращено в очередь. Доставка ещё не подтверждена.')
      await refresh()
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Не удалось вернуть событие в очередь')
    } finally { setActing(false) }
  }

  const shown = (events ?? []).filter(event => filter === 'all' || event.status === filter)
  const eligible = (summary?.pending ?? 0) + (summary?.failed ?? 0)
  return <div className="space-y-4" data-testid="firewall-events-workspace">
    <div className="flex flex-wrap items-start justify-between gap-3">
      <div className="space-y-1">
        <h3 className="text-base font-semibold">Доставка событий устройств</h3>
        <p className="text-sm text-muted-foreground">Статистика по всем событиям; журнал показывает только последние 100 записей. Доставка выполняется отдельно от просмотра.</p>
      </div>
      <Button type="button" size="sm" variant="outline" disabled={loading || acting} onClick={() => void refresh()}>
        {loading ? 'Обновление…' : 'Обновить данные'}
      </Button>
    </div>
    {errors.length > 0 && <div role="alert" className="space-y-1 rounded-md border border-destructive/40 p-3 text-sm text-destructive">
      {errors.map(error => <p key={error}>{error}</p>)}
      <p>Последние успешные данные сохранены; они могут быть устаревшими.</p>
    </div>}
    {loading && events === null && <p role="status" className="text-sm text-muted-foreground">Загрузка журнала и счётчиков…</p>}
    <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 xl:grid-cols-5" aria-label="Статусы доставки">
      {statuses.map(status => <Card key={status} className="min-w-0 px-3 py-3 shadow-none">
        <CardContent className="space-y-1 p-0">
          <p className="text-xl font-semibold tabular-nums">{summary === null ? '—' : summary[status] ?? 0}</p>
          <p className="text-xs text-muted-foreground">{labels[status]}</p>
        </CardContent>
      </Card>)}
    </div>
    <p className="text-xs text-muted-foreground">
      Счётчики: {summaryReadAt?.toLocaleString() ?? 'нет данных'} · Последние события: {eventsReadAt?.toLocaleString() ?? 'нет данных'}
    </p>
    <div className="flex flex-wrap items-center gap-3 rounded-lg border p-3">
      <div className="min-w-0 flex-1 space-y-1">
        <p className="text-sm font-medium">Ручная повторная доставка</p>
        <p className="text-xs text-muted-foreground">До 50 событий со статусом «ожидает» или «ошибка» за один запуск. Исчерпавшие попытки возвращаются в очередь по отдельности.</p>
      </div>
      <AlertDialog>
        <AlertDialogTrigger asChild>
          <Button size="sm" variant="outline" disabled={!canUpdate || acting || loading || !webhookEnabled || summary === null || eligible === 0}>
            Повторить доставку
          </Button>
        </AlertDialogTrigger>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Запустить ручную доставку событий?</AlertDialogTitle>
            <AlertDialogDescription>В очереди или на повторе: {eligible}. Backend попытается доставить до 50 событий на настроенный HTTPS webhook. Внешняя система может получить повторное событие с тем же ID.</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Отмена</AlertDialogCancel>
            <AlertDialogAction onClick={() => void retryPending()}>Запустить попытку</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
    {!webhookEnabled && <p className="text-xs text-muted-foreground">Отправка webhook отключена в текущих настройках редактора. Для реальной доставки сначала сохраните и включите webhook.</p>}
    {result && <p role="status" className="rounded-md border p-3 text-sm">{result}</p>}
    <div className="flex flex-wrap items-center justify-between gap-3">
      <h4 className="text-sm font-semibold">Последние события <span className="font-normal text-muted-foreground">(до 100)</span></h4>
      <Select value={filter} onValueChange={setFilter}>
        <SelectTrigger aria-label="Фильтр статуса события" className="w-full sm:w-52"><SelectValue /></SelectTrigger>
        <SelectContent>
          <SelectItem value="all">Все показанные</SelectItem>
          {statuses.map(status => <SelectItem key={status} value={status}>{labels[status]}</SelectItem>)}
        </SelectContent>
      </Select>
    </div>
    {events && shown.length === 0 && <p className="rounded-md border border-dashed p-6 text-center text-sm text-muted-foreground" role="status">
      {filter === 'all' ? 'Последних событий пока нет.' : 'В загруженном журнале нет событий с таким статусом.'}
    </p>}
    <div className="max-h-[min(65vh,40rem)] space-y-2 overflow-y-auto pr-1" role="list" aria-label="Последние события устройств">
      {shown.map(event => <div key={event.id} role="listitem" className="min-w-0 space-y-2 rounded-lg border p-3 text-sm">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="flex min-w-0 flex-wrap items-center gap-2">
            <strong className="break-all font-medium">{deviceEventLabel(event.event_type)}</strong>
            <Badge variant={event.status === 'exhausted' ? 'red' : event.status === 'delivered' ? 'outline' : 'secondary'}>{statusLabel(event.status)}</Badge>
          </div>
          <span className="shrink-0 text-xs text-muted-foreground">Попытки: {event.attempts}/10</span>
        </div>
        <p className="text-xs text-muted-foreground">Создано: {new Date(event.created_at).toLocaleString()}</p>
        {event.delivered_at && <p className="text-xs text-muted-foreground">Получено подтверждение доставки: {new Date(event.delivered_at).toLocaleString()}</p>}
        {event.next_attempt_at && <p className="text-xs text-muted-foreground">Следующая плановая попытка: {new Date(event.next_attempt_at).toLocaleString()}</p>}
        {event.status === 'exhausted' && <p className="text-xs text-destructive">Автоматические попытки прекращены.</p>}
        {event.last_error && <details className="min-w-0 text-xs text-muted-foreground">
          <summary className="cursor-pointer">Последняя ошибка доставки</summary>
          <p className="mt-2 break-all">{event.last_error}</p>
        </details>}
        {canUpdate && event.status === 'exhausted' && <AlertDialog>
          <AlertDialogTrigger asChild>
            <Button type="button" size="sm" variant="outline" disabled={acting}>Вернуть в очередь</Button>
          </AlertDialogTrigger>
          <AlertDialogContent>
            <AlertDialogHeader>
              <AlertDialogTitle>Вернуть событие в очередь?</AlertDialogTitle>
              <AlertDialogDescription>Попытки доставки этого события будут сброшены. Это не подтверждение получения внешней системой и может привести к повторной обработке.</AlertDialogDescription>
            </AlertDialogHeader>
            <AlertDialogFooter>
              <AlertDialogCancel>Отмена</AlertDialogCancel>
              <AlertDialogAction onClick={() => void requeueExhausted(event)}>Вернуть событие</AlertDialogAction>
            </AlertDialogFooter>
          </AlertDialogContent>
        </AlertDialog>}
      </div>)}
    </div>
  </div>
}
