import { useCallback, useEffect, useRef, useState } from 'react'
import { Button } from '@/components/ui/button'
import { useTranslation } from 'react-i18next'
import { $fetch } from '@/service/http'

export type NodeControlEvent = {
  event_uuid: string
  timestamp: string
  kind: string
  status: string | null
  core_id: string | null
  generation: number | null
}

type EventResponse = { node_uuid: string; events: NodeControlEvent[] }

const titles: Record<string, string> = {
  connected: 'WSS-соединение зарегистрировано',
  disconnected: 'Отключение WSS зарегистрировано',
  approved: 'Регистрация Node подтверждена',
  revoked: 'Регистрация Node отозвана',
  desired_queued: 'Команда поставлена в очередь',
  xray_result: 'Node сообщила результат Xray',
  awg_result: 'Node сообщила результат AWG',
  node_error: 'Сообщение об ошибке Node',
}

export function nodeControlEventTitle(kind: string): string {
  return titles[kind] ?? 'Событие управления Node'
}

export function mostRecentConnectionEvent(events: readonly NodeControlEvent[]): NodeControlEvent | null {
  return events.find(event => event.kind === 'connected' || event.kind === 'disconnected') ?? null
}

/** One explicit history read per diagnostics visit; no streaming or implicit polling. */
export function NodeControlEvents({ nodeUuid, allowed }: { nodeUuid: string; allowed: boolean }) {
  const { t } = useTranslation()
  const [events, setEvents] = useState<NodeControlEvent[] | null>(null)
  const [readAt, setReadAt] = useState<Date | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const revision = useRef(0)

  const refresh = useCallback(async () => {
    if (!allowed) return
    const request = ++revision.current
    setLoading(true)
    setError(null)
    try {
      const result = await $fetch<EventResponse>(`/api/node-registry/nodes/${encodeURIComponent(nodeUuid)}/events?limit=50`)
      if (request !== revision.current) return
      setEvents(result.events)
      setReadAt(new Date())
    } catch (failure) {
      if (request === revision.current) {
        setError(failure instanceof Error ? failure.message : 'Не удалось получить журнал Node')
      }
    } finally {
      if (request === revision.current) setLoading(false)
    }
  }, [nodeUuid, allowed])

  useEffect(() => {
    void refresh()
    return () => { revision.current += 1 }
  }, [refresh])

  const last = events && mostRecentConnectionEvent(events)
  const translateKind = (kind: string) => t(`nodes.controlEvents.kind.${kind}`, { defaultValue: nodeControlEventTitle(kind) })
  return <section className="min-w-0 space-y-3 border-t pt-3" aria-label={t('nodes.controlEvents.title', { defaultValue: 'Журнал управления Node' })}>
    <div className="flex flex-wrap items-center justify-between gap-2">
      <div className="space-y-1">
        <h3 className="text-sm font-semibold">{t('nodes.controlEvents.title', { defaultValue: 'Журнал управления Node' })}</h3>
        <p className="text-xs text-muted-foreground">{t('nodes.controlEvents.description', { defaultValue: 'История событий управления. Не показывает текущий статус соединения.' })}</p>
      </div>
      {allowed && <Button type="button" size="sm" variant="outline" disabled={loading} onClick={() => void refresh()}>
        {t('nodes.controlEvents.refresh', { defaultValue: 'Обновить события' })}
      </Button>}
    </div>
    {!allowed && <p className="text-xs text-muted-foreground">{t('nodes.controlEvents.noAccess', { defaultValue: 'Нет права чтения журналов Node.' })}</p>}
    {allowed && loading && !events && <p role="status" className="text-xs text-muted-foreground">{t('nodes.controlEvents.loading', { defaultValue: 'Загрузка событий…' })}</p>}
    {allowed && error && <p role="alert" className="break-words text-xs text-destructive">{error}</p>}
    {allowed && events && <>
      <p className="text-xs text-muted-foreground">{t('nodes.controlEvents.lastRead', { defaultValue: 'Последняя успешная загрузка' })}: {readAt?.toLocaleString() ?? '—'}</p>
      {last ? <p className="text-xs text-muted-foreground">
        {t('nodes.controlEvents.lastWss', { defaultValue: 'Последняя запись WSS' })}: {translateKind(last.kind).toLowerCase()}
        {' · '}{new Date(last.timestamp).toLocaleString()}. {t('nodes.controlEvents.notLive', { defaultValue: 'Не подтверждает текущего подключения.' })}
      </p> : <p className="text-xs text-muted-foreground">{t('nodes.controlEvents.noWss', { defaultValue: 'События подключения WSS не записаны.' })}</p>}
      {events.length === 0 && <p className="text-xs text-muted-foreground">{t('nodes.controlEvents.empty', { defaultValue: 'Журнал Node пуст.' })}</p>}
      {events.length > 0 && <div className="max-h-80 space-y-2 overflow-y-auto" role="list" aria-label={t('nodes.controlEvents.records', { defaultValue: 'Последние события управления' })}>
        {events.map(event => <div role="listitem" key={event.event_uuid} className="flex min-w-0 flex-wrap justify-between gap-2 rounded-md border p-3 text-xs">
          <div className="min-w-0 space-y-1">
            <p className="font-medium">{translateKind(event.kind)}</p>
            {(event.status || event.generation !== null) && <p className="text-muted-foreground">
              {event.status ?? '—'}{event.generation !== null ? ` · поколение ${event.generation}` : ''}
              {event.core_id ? ` · ${event.core_id}` : ''}
            </p>}
          </div>
          <time className="shrink-0 text-muted-foreground" dateTime={event.timestamp}>{new Date(event.timestamp).toLocaleString()}</time>
        </div>)}
      </div>}
    </>}
  </section>
}
