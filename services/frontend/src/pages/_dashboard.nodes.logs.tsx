import { useCallback, useEffect, useMemo, useState } from 'react'
import { Download, RefreshCw } from 'lucide-react'
import { $fetch } from '@/service/http'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { useAdmin } from '@/hooks/use-admin'
import { hasPermission } from '@/utils/rbac'

type RegisteredNode = {
  node_uuid: string
  display_name: string
  status: string
  last_seen_at?: string | null
}
type NodeEvent = {
  event_uuid: string
  timestamp: string
  kind: string
  status: string | null
  core_id: string | null
  generation: number | null
}

export default function NodeLogs() {
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
      setError('Не удалось получить зарегистрированные Node.')
    }
  }, [allowed])

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
      setError('Не удалось загрузить события управления Node.')
      setEvents([])
    } finally {
      setLoading(false)
    }
  }, [allowed, nodeUUID])

  useEffect(() => { void refreshNodes() }, [refreshNodes])
  useEffect(() => { setEvents([]); void refreshEvents() }, [refreshEvents])

  const visibleEvents = useMemo(() => events.filter(item => {
    const text = [item.kind, item.status, item.core_id, item.generation].join(' ').toLowerCase()
    return (!status || item.status === status) && text.includes(search.toLowerCase())
  }), [events, status, search])

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

  if (!allowed) return <div className="p-4 text-sm text-muted-foreground">Недостаточно прав для просмотра событий Node.</div>

  return (
    <div className="flex flex-col gap-4 p-4">
      <Card>
        <CardHeader><CardTitle className="text-base">События автономных Node</CardTitle></CardHeader>
        <CardContent className="space-y-3">
          <p className="text-muted-foreground text-sm">
            Журнал защищённого WSS-контроля: подтверждения применения конфигураций,
            поколения Xray и статусы. Это не содержимое контейнерных логов и не
            проверка реального VPN-трафика. Данные не содержат клиентских ключей.
          </p>
          <div className="flex flex-wrap items-center gap-2">
            <select
              aria-label="Нода" className="border-input bg-background min-h-9 min-w-48 rounded-md border px-3 text-sm"
              value={nodeUUID} onChange={event => setNodeUUID(event.target.value)}
            >
              {nodes.length === 0 && <option value="">Зарегистрированных Node нет</option>}
              {nodes.map(node => <option key={node.node_uuid} value={node.node_uuid}>
                {node.display_name} ({node.status})
              </option>)}
            </select>
            <Button size="sm" variant="outline" disabled={loading || !nodeUUID}
              onClick={() => void refreshEvents()}>
              <RefreshCw className="mr-2 h-4 w-4" />Обновить
            </Button>
            <Button size="sm" variant="outline" disabled={visibleEvents.length === 0}
              onClick={download}><Download className="mr-2 h-4 w-4" />Экспорт</Button>
          </div>
          <div className="flex flex-wrap gap-2">
            <Input className="max-w-xs" placeholder="Поиск по типу, статусу или Core"
              value={search} onChange={event => setSearch(event.target.value)} />
            <select aria-label="Фильтр статуса"
              className="border-input bg-background min-h-9 rounded-md border px-3 text-sm"
              value={status} onChange={event => setStatus(event.target.value)}>
              <option value="">Все статусы</option>
              <option value="running">running</option>
              <option value="stopped">stopped</option>
              <option value="error">error</option>
              <option value="requires_restart">requires_restart</option>
              <option value="pending">pending</option>
              <option value="accepted">accepted</option>
            </select>
          </div>
          {error && <p role="alert" className="text-destructive text-sm">{error}</p>}
          {loading && <p className="text-muted-foreground text-sm">Загрузка событий…</p>}
          {!loading && visibleEvents.length === 0 && !error && (
            <p className="text-muted-foreground text-sm">Событий пока нет.</p>
          )}
          <div role="log" aria-live="off" className="max-h-[65vh] space-y-1 overflow-y-auto">
            {visibleEvents.map(event => (
              <div key={event.event_uuid} className="grid gap-1 border-b py-2 text-xs sm:grid-cols-[11rem_1fr]">
                <time className="text-muted-foreground" dateTime={event.timestamp}>
                  {new Date(event.timestamp).toLocaleString('ru-RU')}
                </time>
                <div className="min-w-0 break-words font-mono">
                  {event.kind} · {event.core_id ?? 'Agent'} ·
                  {event.generation != null ? ' generation ' + event.generation : ' —'} · {event.status ?? '—'}
                </div>
              </div>
            ))}
          </div>
        </CardContent>
      </Card>
    </div>
  )
}
