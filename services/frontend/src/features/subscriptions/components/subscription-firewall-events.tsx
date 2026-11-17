import { useCallback, useEffect, useRef, useState } from 'react'
import { toast } from 'sonner'
import { useTranslation } from 'react-i18next'
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
  const { t, i18n } = useTranslation()
  const formatDate = (value: Date | string) => new Date(value).toLocaleString(i18n.resolvedLanguage ?? i18n.language)
  const [events, setEvents] = useState<DeviceEvent[] | null>(null)
  const [summary, setSummary] = useState<DeviceEventSummary | null>(null)
  const [eventsReadAt, setEventsReadAt] = useState<Date | null>(null)
  const [summaryReadAt, setSummaryReadAt] = useState<Date | null>(null)
  const [filter, setFilter] = useState('all')
  const [errors, setErrors] = useState<Array<'journalFailed' | 'summaryFailed'>>([])
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
    const problems: Array<'journalFailed' | 'summaryFailed'> = []
    if (rows.status === 'fulfilled') {
      setEvents(rows.value)
      setEventsReadAt(new Date())
    } else problems.push('journalFailed')
    if (totals.status === 'fulfilled') {
      setSummary(totals.value)
      setSummaryReadAt(new Date())
    } else problems.push('summaryFailed')
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
      const detail = `${t('subscriptionFirewall.ui.resultChecked')}: ${outcome.selected}. ${t('subscriptionFirewall.ui.resultDelivered')}: ${outcome.delivered}. ${t('subscriptionFirewall.ui.resultFailed')}: ${outcome.failed ?? outcome.selected - outcome.delivered}.`
      setResult(detail)
      toast.success(t('subscriptionFirewall.ui.retrySuccess'))
      await refresh()
    } catch (error) {
      toast.error(error instanceof Error ? error.message : t('subscriptionFirewall.ui.retryFailed'))
    } finally { setActing(false) }
  }

  async function requeueExhausted(event: DeviceEvent) {
    if (acting || !canUpdate || event.status !== 'exhausted') return
    setActing(true)
    try {
      await $fetch(base + '/device-events/' + encodeURIComponent(event.id) + '/requeue', { method: 'POST' })
      toast.success(t('subscriptionFirewall.ui.requeueSuccess'))
      await refresh()
    } catch (error) {
      toast.error(error instanceof Error ? error.message : t('subscriptionFirewall.ui.requeueFailed'))
    } finally { setActing(false) }
  }

  const shown = (events ?? []).filter(event => filter === 'all' || event.status === filter)
  const eligible = (summary?.pending ?? 0) + (summary?.failed ?? 0)
  return <div className="space-y-4" data-testid="firewall-events-workspace">
    <div className="flex flex-wrap items-start justify-between gap-3">
      <div className="space-y-1">
        <h3 className="text-base font-semibold">{t('subscriptionFirewall.ui.eventsTitle')}</h3>
        <p className="text-sm text-muted-foreground">{t('subscriptionFirewall.ui.eventsDescription')}</p>
      </div>
      <Button type="button" size="sm" variant="outline" disabled={loading || acting} onClick={() => void refresh()}>
        {loading ? t('subscriptionFirewall.ui.refreshing') : t('subscriptionFirewall.ui.refresh')}
      </Button>
    </div>
    {errors.length > 0 && <div role="alert" className="space-y-1 rounded-md border border-destructive/40 p-3 text-sm text-destructive">
      {errors.map(error => <p key={error}>{t(`subscriptionFirewall.ui.${error}`)}</p>)}
      <p>{t('subscriptionFirewall.ui.staleData')}</p>
    </div>}
    {loading && events === null && <p role="status" className="text-sm text-muted-foreground">{t('subscriptionFirewall.ui.loadingEvents')}</p>}
    <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 xl:grid-cols-5" aria-label={t('subscriptionFirewall.ui.countsLabel')}>
      {statuses.map(status => <Card key={status} className="min-w-0 px-3 py-3 shadow-none">
        <CardContent className="space-y-1 p-0">
          <p className="text-xl font-semibold tabular-nums">{summary === null ? '—' : summary[status] ?? 0}</p>
          <p className="text-xs text-muted-foreground">{t(`subscriptionFirewall.ui.delivery.${status}`, { defaultValue: labels[status] })}</p>
        </CardContent>
      </Card>)}
    </div>
    <p className="text-xs text-muted-foreground">
      {t('subscriptionFirewall.ui.summaryTimestamp')}: {summaryReadAt ? formatDate(summaryReadAt) : t('subscriptionFirewall.ui.noData')} · {t('subscriptionFirewall.ui.eventsTimestamp')}: {eventsReadAt ? formatDate(eventsReadAt) : t('subscriptionFirewall.ui.noData')}
    </p>
    <div className="flex flex-wrap items-center gap-3 rounded-lg border p-3">
      <div className="min-w-0 flex-1 space-y-1">
        <p className="text-sm font-medium">{t('subscriptionFirewall.ui.retryTitle')}</p>
        <p className="text-xs text-muted-foreground">{t('subscriptionFirewall.ui.retryDescription')}</p>
      </div>
      <AlertDialog>
        <AlertDialogTrigger asChild>
          <Button size="sm" variant="outline" disabled={!canUpdate || acting || loading || !webhookEnabled || summary === null || eligible === 0}>
            {t('subscriptionFirewall.ui.retry')}
          </Button>
        </AlertDialogTrigger>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{t('subscriptionFirewall.ui.retryDialogTitle')}</AlertDialogTitle>
            <AlertDialogDescription>{t('subscriptionFirewall.ui.retryDialogBefore')}: {eligible}. {t('subscriptionFirewall.ui.retryDialogAfter')}</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>{t('subscriptionFirewall.ui.cancel')}</AlertDialogCancel>
            <AlertDialogAction onClick={() => void retryPending()}>{t('subscriptionFirewall.ui.startRetry')}</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
    {!webhookEnabled && <p className="text-xs text-muted-foreground">{t('subscriptionFirewall.ui.webhookDisabled')}</p>}
    {result && <p role="status" className="rounded-md border p-3 text-sm">{result}</p>}
    <div className="flex flex-wrap items-center justify-between gap-3">
      <h4 className="text-sm font-semibold">{t('subscriptionFirewall.ui.latestEvents')} <span className="font-normal text-muted-foreground">{t('subscriptionFirewall.ui.upToHundred')}</span></h4>
      <Select value={filter} onValueChange={setFilter}>
        <SelectTrigger aria-label={t('subscriptionFirewall.ui.filterLabel')} className="w-full sm:w-52"><SelectValue /></SelectTrigger>
        <SelectContent>
          <SelectItem value="all">{t('subscriptionFirewall.ui.allShown')}</SelectItem>
          {statuses.map(status => <SelectItem key={status} value={status}>{t(`subscriptionFirewall.ui.delivery.${status}`, { defaultValue: labels[status] })}</SelectItem>)}
        </SelectContent>
      </Select>
    </div>
    {events && shown.length === 0 && <p className="rounded-md border border-dashed p-6 text-center text-sm text-muted-foreground" role="status">
      {filter === 'all' ? t('subscriptionFirewall.ui.noneEvents') : t('subscriptionFirewall.ui.noneFiltered')}
    </p>}
    <div className="max-h-[min(65vh,40rem)] space-y-2 overflow-y-auto pr-1" role="list" aria-label={t('subscriptionFirewall.ui.listLabel')}>
      {shown.map(event => <div key={event.id} role="listitem" className="min-w-0 space-y-2 rounded-lg border p-3 text-sm">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="flex min-w-0 flex-wrap items-center gap-2">
            <strong className="break-all font-medium">{t(`subscriptionFirewall.ui.eventKind.${event.event_type}`, { defaultValue: deviceEventLabel(event.event_type) })}</strong>
            <Badge variant={event.status === 'exhausted' ? 'red' : event.status === 'delivered' ? 'outline' : 'secondary'}>{t(`subscriptionFirewall.ui.eventState.${event.status}`, { defaultValue: statusLabel(event.status) })}</Badge>
          </div>
          <span className="shrink-0 text-xs text-muted-foreground">{t('subscriptionFirewall.ui.attempts')}: {event.attempts}/10</span>
        </div>
        <p className="text-xs text-muted-foreground">{t('subscriptionFirewall.ui.created')}: {formatDate(event.created_at)}</p>
        {event.delivered_at && <p className="text-xs text-muted-foreground">{t('subscriptionFirewall.ui.deliveredAt')}: {formatDate(event.delivered_at)}</p>}
        {event.next_attempt_at && <p className="text-xs text-muted-foreground">{t('subscriptionFirewall.ui.nextAttempt')}: {formatDate(event.next_attempt_at)}</p>}
        {event.status === 'exhausted' && <p className="text-xs text-destructive">{t('subscriptionFirewall.ui.autoStopped')}</p>}
        {event.last_error && <details className="min-w-0 text-xs text-muted-foreground">
          <summary className="cursor-pointer">{t('subscriptionFirewall.ui.lastError')}</summary>
          <p className="mt-2 break-all">{event.last_error}</p>
        </details>}
        {canUpdate && event.status === 'exhausted' && <AlertDialog>
          <AlertDialogTrigger asChild>
            <Button type="button" size="sm" variant="outline" disabled={acting}>{t('subscriptionFirewall.ui.returnQueue')}</Button>
          </AlertDialogTrigger>
          <AlertDialogContent>
            <AlertDialogHeader>
              <AlertDialogTitle>{t('subscriptionFirewall.ui.returnDialogTitle')}</AlertDialogTitle>
              <AlertDialogDescription>{t('subscriptionFirewall.ui.returnDialogDesc')}</AlertDialogDescription>
            </AlertDialogHeader>
            <AlertDialogFooter>
              <AlertDialogCancel>{t('subscriptionFirewall.ui.cancel')}</AlertDialogCancel>
              <AlertDialogAction onClick={() => void requeueExhausted(event)}>{t('subscriptionFirewall.ui.returnEvent')}</AlertDialogAction>
            </AlertDialogFooter>
          </AlertDialogContent>
        </AlertDialog>}
      </div>)}
    </div>
  </div>
}
