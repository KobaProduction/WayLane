import { useCallback, useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Download, RefreshCw, Activity, CheckCircle2, AlertTriangle, RadioTower } from 'lucide-react'
import { $fetch } from '@/service/http'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { useAdmin } from '@/hooks/use-admin'
import { hasPermission } from '@/utils/rbac'

export type RegisteredNode = {
  node_uuid: string
  display_name: string
  status: string
  last_seen_at?: string | null
}
export type NodeEvent = {
  event_uuid: string
  timestamp: string
  kind: string
  status: string | null
  core_id: string | null
  generation: number | null
}

const statuses = ['running', 'stopped', 'error', 'requires_restart', 'pending', 'accepted', 'connected', 'disconnected']
const eventVariant = (status: string | null) => {
  if (status === 'error' || status === 'requires_restart') return 'red' as const
  if (status === 'running' || status === 'connected' || status === 'accepted') return 'green' as const
  if (status === 'pending' || status === 'disconnected') return 'yellow' as const
  return 'outline' as const
}

export default function NodeLogs() {
  const { t, i18n } = useTranslation()
  const { admin } = useAdmin()
  const allowed = hasPermission(admin, 'nodes', 'logs')
  const [nodes, setNodes] = useState<RegisteredNode[]>([])
  const [nodeUUID, setNodeUUID] = useState('')
  const [events, setEvents] = useState<NodeEvent[]>([])
  const [search, setSearch] = useState('')
  const [status, setStatus] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const refreshNodes = useCallback(async () => {
    if (!allowed) return
    try {
      const result = await $fetch<{ nodes: RegisteredNode[] }>('/api/node-registry/nodes')
      setNodes(result.nodes)
      setNodeUUID(current => result.nodes.some(item => item.node_uuid === current)
        ? current : (result.nodes[0]?.node_uuid ?? ''))
    } catch {
      setError(t('nodes.logs.nodeLoadError'))
    }
  }, [allowed, t])

  const refreshEvents = useCallback(async () => {
    if (!allowed || !nodeUUID) return
    setLoading(true)
    setError('')
    try {
      const result = await $fetch<{ node_uuid: string; events: NodeEvent[] }>(
        '/api/node-registry/nodes/' + encodeURIComponent(nodeUUID) + '/events',
        { query: { limit: 500 } },
      )
      setEvents(result.events)
    } catch {
      setError(t('nodes.logs.loadError'))
      setEvents([])
    } finally { setLoading(false) }
  }, [allowed, nodeUUID, t])

  useEffect(() => { void refreshNodes() }, [refreshNodes])
  useEffect(() => { setEvents([]); void refreshEvents() }, [refreshEvents])

  const eventTitle = (event: NodeEvent) => t(`nodes.logs.event.${event.kind}`, { defaultValue: event.kind })
  const statusTitle = (code: string) => t(`nodes.logs.status.${code}`, { defaultValue: code })
  const visibleEvents = useMemo(() => events.filter(item => {
    const text = [eventTitle(item), item.kind, item.status && statusTitle(item.status),
      item.status, item.core_id, item.generation].join(' ').toLocaleLowerCase()
    return (!status || item.status === status) && text.includes(search.trim().toLocaleLowerCase())
  }), [events, status, search, i18n.language])
  const locale = ({ ru: 'ru-RU', en: 'en-US', zh: 'zh-CN', fa: 'fa-IR' } as Record<string, string>)[i18n.language.slice(0, 2)] ?? 'en-US'
  const selectedNode = nodes.find(item => item.node_uuid === nodeUUID)

  const download = () => {
    const body = visibleEvents.map(item =>
      [item.timestamp, item.kind, item.core_id ?? '-', item.generation ?? '-', item.status ?? '-'].join(' | ')
    ).join('\n')
    const url = URL.createObjectURL(new Blob([body], { type: 'text/plain;charset=utf-8' }))
    const anchor = document.createElement('a')
    anchor.href = url
    anchor.download = 'waylane-node-events-' + new Date().toISOString().slice(0, 10) + '.txt'
    anchor.click()
    URL.revokeObjectURL(url)
  }

  if (!allowed) return <div className="p-6 text-sm text-muted-foreground">{t('nodes.logs.noAccess')}</div>

  return (
    <div className="min-w-0 p-4">
      <Card className="min-w-0 shadow-sm">
        <CardHeader className="gap-2">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="space-y-1">
              <CardTitle className="text-base">{t('nodes.logs.eventsTitle')}</CardTitle>
              <CardDescription className="max-w-2xl">{t('nodes.logs.eventsDescription')}</CardDescription>
            </div>
            <Badge variant="outline">{t('nodes.logs.count', { count: visibleEvents.length })}</Badge>
          </div>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="flex flex-wrap items-end gap-2">
            <div className="min-w-52 flex-1 space-y-1 sm:flex-none">
              <Label htmlFor="events-node-select">{t('nodes.logs.nodeSelect')}</Label>
              <select id="events-node-select" aria-label={t('nodes.logs.nodeSelect')}
                className="border-input bg-background min-h-10 w-full min-w-52 rounded-md border px-3 text-sm"
                value={nodeUUID} onChange={event => setNodeUUID(event.target.value)}>
                {nodes.length === 0 && <option value="">{t('nodes.logs.noNodes')}</option>}
                {nodes.map(node => <option key={node.node_uuid} value={node.node_uuid}>{node.display_name}</option>)}
              </select>
            </div>
            {selectedNode && (
              <Badge variant={selectedNode.status === 'approved' ? 'green' : 'outline'} className="mb-2">
                {t(`nodes.nodeStatus.${selectedNode.status}`, { defaultValue: selectedNode.status })}
              </Badge>
            )}
            <div className="flex w-full min-w-0 flex-wrap gap-2 sm:ml-auto sm:w-auto">
              <Button className="min-w-0 flex-1 sm:flex-none" size="sm" variant="outline" disabled={loading || !nodeUUID}
                onClick={() => void refreshEvents()}>
                <RefreshCw className={`mr-2 h-4 w-4 ${loading ? 'animate-spin' : ''}`} />{t('nodes.logs.reload')}
              </Button>
              <Button className="min-w-0 flex-1 sm:flex-none" size="sm" variant="outline" disabled={visibleEvents.length === 0}
                onClick={download}><Download className="mr-2 h-4 w-4" />{t('nodes.logs.export')}</Button>
            </div>
          </div>
          <div className="grid gap-2 sm:grid-cols-[minmax(0,1fr)_15rem]">
            <Input aria-label={t('nodes.logs.searchPlaceholder')} placeholder={t('nodes.logs.searchPlaceholder')}
              value={search} onChange={event => setSearch(event.target.value)} />
            <select aria-label={t('nodes.logs.allStatuses')}
              className="border-input bg-background min-h-10 w-full rounded-md border px-3 text-sm"
              value={status} onChange={event => setStatus(event.target.value)}>
              <option value="">{t('nodes.logs.allStatuses')}</option>
              {statuses.map(value => <option key={value} value={value}>{statusTitle(value)}</option>)}
            </select>
          </div>
          {error && <p role="alert" className="text-destructive text-sm">{error}</p>}
          {loading && <p role="status" className="text-muted-foreground text-sm">{t('nodes.logs.reload')}…</p>}
          {!loading && visibleEvents.length === 0 && !error && (
            <p className="rounded-md border border-dashed px-4 py-8 text-center text-sm text-muted-foreground">
              {events.length === 0 ? t('nodes.logs.none') : t('nodes.logs.empty')}
            </p>
          )}
          <ol role="log" aria-live="off" className="max-h-[65vh] space-y-2 overflow-y-auto pr-1">
            {visibleEvents.map(event => {
              const isError = event.status === 'error' || event.status === 'requires_restart'
              const Icon = isError ? AlertTriangle : event.kind.includes('connect') ? RadioTower :
                event.status === 'running' || event.status === 'accepted' ? CheckCircle2 : Activity
              return (
                <li key={event.event_uuid} className="flex min-w-0 items-start gap-3 rounded-lg border p-3">
                  <div className="mt-0.5 rounded-full bg-muted p-2"><Icon className="h-4 w-4" aria-hidden /></div>
                  <div className="min-w-0 flex-1 space-y-2">
                    <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
                      <span className="font-medium text-sm">{eventTitle(event)}</span>
                      {event.status && <Badge variant={eventVariant(event.status)}>{statusTitle(event.status)}</Badge>}
                      <time dateTime={event.timestamp} className="text-muted-foreground ml-auto text-xs">
                        {new Date(event.timestamp).toLocaleString(locale)}
                      </time>
                    </div>
                    {event.core_id && <p className="text-muted-foreground text-xs">{t('nodes.logs.core')}: {event.core_id}</p>}
                    <details className="text-xs text-muted-foreground">
                      <summary className="w-fit cursor-pointer rounded underline-offset-2 hover:underline focus-visible:outline-2 focus-visible:outline-primary">
                        {t('nodes.logs.details')}
                      </summary>
                      <dl className="mt-2 grid min-w-0 grid-cols-[auto_1fr] gap-x-3 gap-y-1 rounded bg-muted/40 p-3 font-mono">
                        <dt>{t('nodes.logs.source')}</dt><dd className="break-all">{event.kind}</dd>
                        <dt>{t('nodes.logs.core')}</dt><dd className="break-all">{event.core_id ?? '—'}</dd>
                        <dt>{t('nodes.logs.generation')}</dt><dd>{event.generation ?? '—'}</dd>
                        <dt>{t('nodes.logs.allStatuses')}</dt><dd>{event.status ?? '—'}</dd>
                      </dl>
                    </details>
                  </div>
                </li>
              )
            })}
          </ol>
        </CardContent>
      </Card>
    </div>
  )
}
