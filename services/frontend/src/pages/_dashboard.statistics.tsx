import { useCallback, useEffect, useMemo, useState } from 'react'
import { CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import PageHeader from '@/components/layout/page-header'
import { Separator } from '@/components/ui/separator'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import { Input } from '@/components/ui/input'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { $fetch } from '@/service/http'
import { useAdmin } from '@/hooks/use-admin'
import { formatBytes } from '@/utils/formatByte'


type Node = { id: number; name: string; status: string; core_id: number | null }
type SubscriptionItem = { uuid: string; status: string; online_at: string | null }
type Catalog = { nodes: Node[]; subscriptions: SubscriptionItem[]; catalog_truncated: boolean }
type MatrixRow = { subscription_uuid: string; status: string; node_id: number; node_name: string; total_bytes: number; last_recorded_at: string | null }
type Matrix = { rows: MatrixRow[]; truncated: boolean; direction_available: boolean }
type TrafficSeries = {
  series: { id: string | number; name: string; total_bytes: number }[]
  rows: { period_start: string; series_id: string | number; total_bytes: number }[]
  truncated: boolean
}
type HistoryRow = { period_start: string; node_id: string | number; node_name: string; total_bytes: number; upload: number | null; download: number | null }
type History = { rows: HistoryRow[]; truncated: boolean; direction_available: boolean; source: string }
type DeviceEntry = { name: string; count: number }
type Devices = {
  systems: DeviceEntry[]; versions: DeviceEntry[]; models: DeviceEntry[]; user_agents: DeviceEntry[]
  recent: { subscription_uuid: string; system: string | null; version: string | null; model: string | null; last_used_at: string | null }[]
}
type AutonomousNode = {
  node_uuid: string
  display_name: string
  status: string
  wss_fresh: boolean
  last_seen_at: string | null
  cpu_logical_count: number | null
  memory_total_bytes: number | null
  memory_available_bytes: number | null
  uptime_seconds: number | null
  total_uplink: string
  total_downlink: string
}
type AutonomousSummary = {
  billing_authoritative: false
  traffic_kind: 'cumulative_xray_stats_not_bandwidth_speed'
  nodes: AutonomousNode[]
  registered_nodes: number
  fresh_wss_nodes: number
}
type AutonomousHours = {
  billing_authoritative: false
  granularity: string
  rows: {
    node_uuid: string
    subscription_uuid: string
    bucket_start: string
    uplink: string
    downlink: string
  }[]
}
type Settings = { excludedNodes: number[]; excludedSubscriptions: string[]; colors: Record<string, string> }
type Direction = 'total' | 'upload' | 'download'
const base = '/api/business-analytics'
const palette = ['#2563eb', '#dc2626', '#16a34a', '#9333ea', '#ea580c', '#0891b2', '#be185d', '#ca8a04', '#64748b', '#0d9488']
const fmt = (n: number) => formatBytes(n, 2)

const errText = (e: unknown) => e instanceof Error ? e.message : 'Ошибка загрузки'
const asDate = (d: Date) => d.toISOString().slice(0, 10)
function loadSettings(): Settings {
  try {
    const x = JSON.parse(localStorage.getItem('waylane:analytics-view') || '{}') as Partial<Settings>
    return { excludedNodes: x.excludedNodes || [], excludedSubscriptions: x.excludedSubscriptions || [], colors: x.colors || {} }
  } catch { return { excludedNodes: [], excludedSubscriptions: [], colors: {} } }
}
function Group({ title, rows }: { title: string; rows: DeviceEntry[] }) {
  return <Card><CardHeader><CardTitle>{title}</CardTitle></CardHeader><CardContent className="space-y-2">
    {rows.map(x => <p key={x.name} className="flex justify-between gap-3 border-b py-1 text-sm"><span className="truncate">{x.name}</span><b>{x.count}</b></p>)}
    {!rows.length && <p className="text-sm text-muted-foreground">Нет данных.</p>}
  </CardContent></Card>
}
function HistoryChart({ rows, nodes, direction, colorOf }: {
  rows: HistoryRow[]; nodes: { id: number | string; name: string }[]; direction: Direction; colorOf: (id: number | string) => string
}) {
  const chart = useMemo(() => {
    const dates = new Map<string, Record<string, string | number>>()
    const ids = new Set<number | string>()
    for (const r of rows) {
      ids.add(r.node_id)
      const p = dates.get(r.period_start) || { date: r.period_start }
      const key = 'node-' + r.node_id
      p[key] = Number(p[key] || 0) + (direction === 'total' ? r.total_bytes : r[direction] || 0) / (1024 ** 3)
      dates.set(r.period_start, p)
    }
    return { values: Array.from(dates.values()).sort((a, b) => String(a.date).localeCompare(String(b.date))),
      ids: Array.from(ids).slice(0, 20) }
  }, [rows, direction])
  if (!chart.values.length) return <p className="p-4 text-sm text-muted-foreground">Нет записей за выбранный период.</p>
  return <div className="h-80 w-full"><ResponsiveContainer width="100%" height="100%">
    <LineChart data={chart.values}>
      <CartesianGrid strokeDasharray="3 3" opacity={0.3} /><XAxis dataKey="date" tick={{ fontSize: 10 }} minTickGap={35} />
      <YAxis tick={{ fontSize: 11 }} width={50} /><Tooltip formatter={value => Number(value).toFixed(3) + ' GiB'} /><Legend />
      {chart.ids.map(id => <Line key={id} type="monotone" dot={false} connectNulls={false} dataKey={'node-' + id}
        name={nodes.find(n => n.id === id)?.name || '#' + id} stroke={colorOf(id)} strokeWidth={2} />)}
    </LineChart>
  </ResponsiveContainer></div>
}

export default function Statistics() {
  const { admin } = useAdmin()
  const canRead = admin?.role?.is_owner === true
  const [tab, setTab] = useState('overview')
  const [span, setSpan] = useState('30')
  const [from, setFrom] = useState(asDate(new Date(Date.now() - 30 * 86400000)))
  const [to, setTo] = useState(asDate(new Date()))
  const [period, setPeriod] = useState<'hour' | 'day'>('day')
  const [subscriptionUuid, setSubscriptionUuid] = useState('')
  const [nodeId, setNodeId] = useState('')
  const [status, setStatus] = useState('')
  const [orientation, setOrientation] = useState<'node' | 'subscription'>('node')
  const [metric, setMetric] = useState<Direction>('total')
  const [chosenNodes, setChosenNodes] = useState<number[]>([])
  const [showExcluded, setShowExcluded] = useState(false)
  const [settings, setSettings] = useState(loadSettings)
  const [catalog, setCatalog] = useState<Catalog>({ nodes: [], subscriptions: [], catalog_truncated: false })
  const [matrix, setMatrix] = useState<Matrix>({ rows: [], truncated: false, direction_available: false })
  const [trafficSeries, setTrafficSeries] = useState<TrafficSeries>({ series: [], rows: [], truncated: false })
  const [history, setHistory] = useState<History>({ rows: [], truncated: false, direction_available: true, source: 'node_usages' })
  const [devices, setDevices] = useState<Devices | null>(null)
  const [autonomous, setAutonomous] = useState<AutonomousSummary | null>(null)
  const [autonomousHours, setAutonomousHours] = useState<AutonomousHours | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const excludedNodes = showExcluded ? [] : settings.excludedNodes
  const excludedSubscriptions = showExcluded ? [] : settings.excludedSubscriptions
  const colorOf = useCallback((id: number | string) => {
    const seed = typeof id === 'number' ? id : Array.from(id).reduce((value, char) => (value * 31 + char.charCodeAt(0)) >>> 0, 0)
    return settings.colors[String(id)] || palette[seed % palette.length]
  }, [settings.colors])

  useEffect(() => { localStorage.setItem('waylane:analytics-view', JSON.stringify(settings)) }, [settings])
  useEffect(() => {
    if (!canRead) return
    void $fetch<Catalog>(base + '/catalog').then(setCatalog).catch(e => setError(errText(e)))
  }, [canRead])

  const query = useMemo(() => {
    const q = new URLSearchParams()
    if (span !== 'all') {
      const end = span === 'custom' ? new Date(to + 'T23:59:59Z') : new Date()
      const start = span === 'custom' ? new Date(from + 'T00:00:00Z') : new Date(end.getTime() - Number(span) * 86400000)
      q.set('start', start.toISOString()); q.set('end', end.toISOString())
    }
    return q
  }, [span, from, to])
  const excludedNodeKey = excludedNodes.join(',')
  const excludedSubscriptionKey = excludedSubscriptions.join(',')
  const chosenNodeKey = chosenNodes.join(',')

  const refresh = useCallback(async () => {
    if (!canRead) return
    setLoading(true); setError('')
    try {
      if (tab === 'devices') setDevices(await $fetch<Devices>(base + '/devices?' + query))
      else if (tab === 'subscriptions') {
        const q = new URLSearchParams(query)
        if (span === 'all') q.set('all_time', 'true')
        if (nodeId) q.set('node_id', nodeId)
        if (subscriptionUuid) q.set('subscription_uuid', subscriptionUuid)
        if (status) q.set('status', status)
        if (excludedNodeKey) q.set('exclude_node_ids', excludedNodeKey)
        if (excludedSubscriptionKey) q.set('exclude_subscription_uuids', excludedSubscriptionKey)
        setMatrix(await $fetch<Matrix>(base + '/matrix?' + q))
        if (nodeId || subscriptionUuid) {
          const seriesQuery = new URLSearchParams(q)
          seriesQuery.delete('all_time')
          if (span === 'all') {
            seriesQuery.set('start', new Date(Date.now() - 90 * 86400000).toISOString())
            seriesQuery.set('end', new Date().toISOString())
          }
          seriesQuery.set('period', period)
          setTrafficSeries(await $fetch<TrafficSeries>(base + '/traffic-series?' + seriesQuery))
        } else setTrafficSeries({ rows: [], series: [], truncated: false })
      } else if (tab === 'overview' || tab === 'nodes') {
        const q = new URLSearchParams(query)
        if (span === 'all') {
          q.set('start', new Date(Date.now() - 90 * 86400000).toISOString())
          q.set('end', new Date().toISOString())
        }
        q.set('period', period)
        if (chosenNodeKey) q.set('node_ids', chosenNodeKey)
        if (excludedNodeKey) q.set('exclude_node_ids', excludedNodeKey)
        if (excludedSubscriptionKey) q.set('exclude_subscription_uuids', excludedSubscriptionKey)
        setHistory(await $fetch<History>(base + '/nodes?' + q))
      }
    } catch (e) { setError(errText(e)) }
    finally { setLoading(false) }
  }, [canRead, tab, query, span, nodeId, subscriptionUuid, status, period, chosenNodeKey, excludedNodeKey, excludedSubscriptionKey])
  useEffect(() => { void refresh() }, [refresh])

  const refreshAutonomous = useCallback(async () => {
    if (!canRead || !['overview', 'nodes'].includes(tab)) return
    try {
      const [summary, hours] = await Promise.all([
        $fetch<AutonomousSummary>('/api/node-registry/analytics/summary'),
        $fetch<AutonomousHours>('/api/node-registry/analytics/usage/hours', { query: { days: 30 } }),
      ])
      setAutonomous(summary)
      setAutonomousHours(hours)
    } catch {
      setAutonomous(null)
      setAutonomousHours(null)
    }
  }, [canRead, tab])
  useEffect(() => { void refreshAutonomous() }, [refreshAutonomous])

  const autonomousNodes = autonomous?.nodes ?? []
  const groups = useMemo(() => {
    const grouped = new Map<string, { name: string; total: number; details: { name: string; total: number }[] }>()
    for (const r of matrix.rows) {
      const parent = orientation === 'node' ? String(r.node_id) : r.subscription_uuid
      const parentName = orientation === 'node' ? r.node_name : r.subscription_uuid
      const childName = orientation === 'node' ? r.subscription_uuid : r.node_name
      const group = grouped.get(parent) || { name: parentName, total: 0, details: [] }
      group.total += r.total_bytes
      group.details.push({ name: childName, total: r.total_bytes })
      grouped.set(parent, group)
    }
    return Array.from(grouped.values()).sort((a, b) => b.total - a.total)
  }, [matrix.rows, orientation])
  function toggleNodeExclusion(id: number, include: boolean) {
    setSettings(p => ({ ...p, excludedNodes: include ? p.excludedNodes.filter(value => value !== id) : [...p.excludedNodes, id] }))
  }
  function toggleSubscriptionExclusion(id: string, include: boolean) {
    setSettings(p => ({ ...p, excludedSubscriptions: include ? p.excludedSubscriptions.filter(value => value !== id) : [...p.excludedSubscriptions, id] }))
  }
  if (!canRead) return <p className="p-6 text-sm">Общая статистика всех подписок доступна владельцу панели.</p>
  return <div className="w-full space-y-4 pb-6">
    <PageHeader title="statistics" description="monitorServers" /><Separator />
    <div className="space-y-4 px-3 sm:px-4">
      <Tabs value={tab} onValueChange={setTab}>
        <TabsList className="h-auto flex-wrap">
          <TabsTrigger value="overview">Общая</TabsTrigger>
          <TabsTrigger value="nodes">Ноды и архив</TabsTrigger>
          <TabsTrigger value="subscriptions">Трафик подписок</TabsTrigger>
          <TabsTrigger value="devices">Устройства</TabsTrigger>
          <TabsTrigger value="filters">Фильтры и цвета</TabsTrigger>
        </TabsList>
        <Card className="mt-4"><CardContent className="flex flex-wrap items-end gap-3 p-4">
          <label className="text-sm">Период
            <select className="mt-1 block h-9 rounded border bg-background px-2" value={span} onChange={e => setSpan(e.target.value)}>
              <option value="1">24 часа</option><option value="7">7 дней</option><option value="30">30 дней</option>
              <option value="90">90 дней</option><option value="custom">Свой</option>
              <option value="all">Всё время (матрица)</option>
            </select>
          </label>
          {span === 'custom' && <>
            <label className="text-sm">От<Input type="date" value={from} onChange={e => setFrom(e.target.value)} /></label>
            <label className="text-sm">До<Input type="date" value={to} onChange={e => setTo(e.target.value)} /></label>
          </>}
          {['overview', 'nodes'].includes(tab) && <label className="text-sm">Группировка
            <select className="mt-1 block h-9 rounded border bg-background px-2" value={period} onChange={e => setPeriod(e.target.value as typeof period)}>
              <option value="hour">Часы</option><option value="day">Дни</option>
            </select>
          </label>}
          <label className="flex items-center gap-2 pb-2 text-sm">
            <Checkbox checked={showExcluded} onCheckedChange={x => setShowExcluded(x === true)} />Учитывать служебные ноды и подписок
          </label>
          <Button variant="outline" onClick={() => void refresh()} disabled={loading}>{loading ? 'Загрузка…' : 'Обновить'}</Button>
        </CardContent></Card>
        {error && <p className="rounded border border-destructive p-3 text-sm text-destructive">{error}</p>}
        {(history.truncated || matrix.truncated) && <p className="text-xs text-muted-foreground">Часть агрегатов ограничена для производительности; уточните фильтр.</p>}
        <TabsContent value="overview" className="mt-4 space-y-4">
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            {[
              { label: 'Автономные Node', value: String(autonomous?.registered_nodes ?? '—') },
              { label: 'Node с актуальным WSS', value: String(autonomous?.fresh_wss_nodes ?? '—') },
              { label: 'Xray upload · накоплено', value: autonomous
                ? autonomousNodes.reduce((sum, n) => sum + BigInt(n.total_uplink), 0n).toLocaleString('ru-RU') + ' Б' : '—' },
              { label: 'Xray download · накоплено', value: autonomous
                ? autonomousNodes.reduce((sum, n) => sum + BigInt(n.total_downlink), 0n).toLocaleString('ru-RU') + ' Б' : '—' },
            ].map(x => <Card key={x.label}><CardContent className="p-5">
              <p className="text-sm text-muted-foreground">{x.label}</p><p className="mt-1 text-xl font-semibold">{x.value}</p>
            </CardContent></Card>)}
          </div>
          <Card><CardHeader><CardTitle>Автономные Node — телеметрия WSS</CardTitle></CardHeader>
            <CardContent className="space-y-3">
              <p className="text-xs text-muted-foreground">
                Счётчики являются накопленными наблюдениями Xray, не текущей скоростью и не расчётом оплаты.
                Точное количество открытых VPN-сессий этот источник не измеряет.
              </p>
              <Button size="sm" variant="outline" onClick={() => void refreshAutonomous()}>
                Обновить телеметрию
              </Button>
              {autonomousNodes.map(node => <div key={node.node_uuid} className="rounded border p-3 text-sm">
                <p className="font-medium">{node.display_name} · {node.wss_fresh ? 'WSS активен' : 'WSS не подтверждён'}</p>
                <p className="text-muted-foreground text-xs">
                  {node.cpu_logical_count ?? '—'} CPU ·
                  RAM {node.memory_available_bytes != null ? fmt(node.memory_available_bytes) : '—'}
                  / {node.memory_total_bytes != null ? fmt(node.memory_total_bytes) : '—'} ·
                  последний heartbeat {node.last_seen_at ? new Date(node.last_seen_at).toLocaleString('ru-RU') : 'нет'}
                </p>
                <p className="break-all font-mono text-xs">{node.node_uuid}</p>
              </div>)}
              {autonomousNodes.length === 0 && <p className="text-sm text-muted-foreground">
                Нет доступных записей Node Registry.
              </p>}
            </CardContent></Card>
          <Card><CardHeader><CardTitle>Исторический трафик PasarGuard (архив)</CardTitle></CardHeader><CardContent>
            <HistoryChart rows={history.rows} nodes={catalog.nodes} direction="total" colorOf={colorOf} />
            <p className="text-xs text-muted-foreground">
              Это ранее накопленные данные числовых Node. Они не считаются статистикой новых UUID Node.
            </p>
          </CardContent></Card>
        </TabsContent>
        <TabsContent value="nodes" className="mt-4 space-y-4">
          <Card>
            <CardHeader><CardTitle>Трафик автономных Node по UTC-часам</CardTitle></CardHeader>
            <CardContent className="space-y-3">
              <p className="text-muted-foreground text-xs">
                Источник: приватный Xray StatsService через WSS. Часы обозначают время
                получения показаний, а не точное время передачи каждого байта.
                Данные не являются измерением текущей скорости соединения.
              </p>
              <Button variant="outline" size="sm" onClick={() => void refreshAutonomous()}>
                Обновить историю Xray
              </Button>
              {!autonomousHours?.rows.length && <p className="text-sm text-muted-foreground">
                Данных по автономным Node пока нет.
              </p>}
              {autonomousHours?.rows.slice(0, 30).map(row => (
                <div key={row.node_uuid + row.subscription_uuid + row.bucket_start}
                  className="space-y-1 border-b pb-2 text-xs">
                  <p>{autonomousNodes.find(n => n.node_uuid === row.node_uuid)?.display_name ?? row.node_uuid}
                    {' · '}{new Date(row.bucket_start).toLocaleString('ru-RU')}</p>
                  <p className="break-all font-mono text-muted-foreground">{row.subscription_uuid}</p>
                  <p>↑ {BigInt(row.uplink).toLocaleString('ru-RU')} Б ·
                    {' '}↓ {BigInt(row.downlink).toLocaleString('ru-RU')} Б</p>
                </div>
              ))}
              {(autonomousHours?.rows.length ?? 0) > 30 && (
                <p className="text-muted-foreground text-xs">Показаны 30 последних записей.</p>
              )}
            </CardContent>
          </Card>
          <Card><CardHeader><CardTitle>Исторические записи PasarGuard (архив)</CardTitle></CardHeader><CardContent className="space-y-3">
            <label className="text-sm">Показатель
              <select className="ml-2 rounded border bg-background p-2" value={metric} onChange={e => setMetric(e.target.value as Direction)}>
                <option value="total">Всего</option>
                <option value="upload" disabled={!history.direction_available}>Upload</option>
                <option value="download" disabled={!history.direction_available}>Download</option>
              </select>
            </label>
            <div className="flex flex-wrap gap-2">
              {catalog.nodes.map(node => <label key={node.id} className="flex items-center gap-2 rounded border px-2 py-1 text-sm">
                <Checkbox checked={!chosenNodes.length || chosenNodes.includes(node.id)} onCheckedChange={checked => {
                  const nodes = chosenNodes.length ? chosenNodes : catalog.nodes.slice(0, 20).map(n => n.id)
                  setChosenNodes(checked === true ? [...new Set([...nodes, node.id])].slice(0, 20) : nodes.filter(id => id !== node.id))
                }} /><span style={{ color: colorOf(node.id) }}>{node.name}</span>
              </label>)}
            </div>
            <HistoryChart rows={history.rows} nodes={catalog.nodes} direction={history.direction_available ? metric : 'total'} colorOf={colorOf} />
            {!history.direction_available && <p className="text-sm text-muted-foreground">
              При исключении мостовых подписок показывается только суммарный трафик, без разделения по направлениям.
            </p>}
          </CardContent></Card>
        </TabsContent>
        <TabsContent value="subscriptions" className="mt-4 space-y-4">
          <Card><CardHeader><CardTitle>Нода ↔ Подписка: суммарный трафик</CardTitle></CardHeader><CardContent className="space-y-4">
            <div className="flex flex-wrap gap-3 text-sm">
              <label>Просмотр
                <select className="ml-2 rounded border bg-background p-2" value={orientation} onChange={e => setOrientation(e.target.value as typeof orientation)}>
                  <option value="node">Нода → подписки</option><option value="subscription">Подписка → ноды</option>
                </select>
              </label>
              <label>Нода
                <select className="ml-2 max-w-52 rounded border bg-background p-2" value={nodeId} onChange={e => setNodeId(e.target.value)}>
                  <option value="">Все</option>{catalog.nodes.map(n => <option value={n.id} key={n.id}>{n.name}</option>)}
                </select>
              </label>
              <label>Подписка
                <select className="ml-2 max-w-52 rounded border bg-background p-2" value={subscriptionUuid} onChange={e => setSubscriptionUuid(e.target.value)}>
                  <option value="">Все</option>{catalog.subscriptions.map(u => <option value={u.uuid} key={u.uuid}>{u.uuid}</option>)}
                </select>
              </label>
              <label>Статус
                <select className="ml-2 rounded border bg-background p-2" value={status} onChange={e => setStatus(e.target.value)}>
                  <option value="">Все</option>{['active', 'disabled', 'on_hold', 'limited', 'expired'].map(s => <option key={s} value={s}>{s}</option>)}
                </select>
              </label>
            </div>
            <p className="text-sm">Трафик выбранных связей: <strong>{fmt(matrix.rows.reduce((sum, x) => sum + x.total_bytes, 0))}</strong></p>
            <p className="text-xs text-muted-foreground">Из существующей истории можно получить только сумму для пары подписка–нода. Upload/download подписки не сохраняются отдельно.</p>
            {(nodeId || subscriptionUuid) && <div className="rounded border p-3">
              <h4 className="mb-2 font-medium">
                {subscriptionUuid ? 'История подписки по нодам' : 'История ноды по подпискам'}
              </h4>
              {trafficSeries.truncated && <p className="text-xs text-amber-600">Временные данные ограничены; уточните период.</p>}
              <HistoryChart direction="total" colorOf={colorOf}
                nodes={trafficSeries.series.map(item => ({ id: item.id, name: item.name, status: '', core_id: null }))}
                rows={trafficSeries.rows.map(row => ({
                  period_start: row.period_start, node_id: row.series_id,
                  node_name: trafficSeries.series.find(item => item.id === row.series_id)?.name || String(row.series_id),
                  total_bytes: row.total_bytes, upload: null, download: null,
                }))} />
              <p className="text-xs text-muted-foreground">Топ-12 серий по объёму, за выбранный интервал. Только суммарный трафик.</p>
            </div>}
            <div className="max-h-[34rem] space-y-2 overflow-auto">
              {groups.map(group => <details key={group.name} className="rounded border p-3">
                <summary className="flex cursor-pointer justify-between gap-3 font-medium">
                  <span>{group.name}</span><span>{fmt(group.total)}</span>
                </summary>
                <div className="mt-3 space-y-1">{group.details.sort((a, b) => b.total - a.total).map(item =>
                  <p className="flex justify-between gap-2 border-b py-1 text-sm" key={item.name}>
                    <span>{item.name}</span><span>{fmt(item.total)}</span>
                  </p>)}</div>
              </details>)}
              {!groups.length && <p className="text-sm text-muted-foreground">Нет данных по этим фильтрам.</p>}
            </div>
          </CardContent></Card>
        </TabsContent>
        <TabsContent value="devices" className="mt-4 space-y-4">
          <div className="grid gap-3 md:grid-cols-2">
            <Group title="Операционные системы" rows={devices?.systems || []} />
            <Group title="Версии систем" rows={devices?.versions || []} />
            <Group title="Модели устройств" rows={devices?.models || []} />
            <Group title="User-Agent подписок" rows={devices?.user_agents || []} />
          </div>
          <Card><CardHeader><CardTitle>Последние обновления устройств</CardTitle></CardHeader><CardContent className="max-h-80 overflow-auto">
            {devices?.recent.map((r, i) => <p className="flex flex-wrap justify-between gap-3 border-b py-2 text-sm" key={i}>
              <span>{r.subscription_uuid} · {[r.system, r.version, r.model].filter(Boolean).join(' / ')}</span>
              <span>{r.last_used_at ? new Date(r.last_used_at).toLocaleString() : 'Нет данных'}</span>
            </p>)}
            <p className="mt-2 text-xs text-muted-foreground">HWID — текущее состояние; User-Agent — обновления подписки, не VPN-сессии.</p>
          </CardContent></Card>
        </TabsContent>
        <TabsContent value="filters" className="mt-4 space-y-4">
          <Card><CardHeader><CardTitle>Транзитные ноды и цвета</CardTitle></CardHeader><CardContent className="max-h-80 space-y-2 overflow-auto">
            {catalog.nodes.map(n => <div className="flex items-center justify-between gap-2 border-b py-2" key={n.id}>
              <span className="text-sm">{n.name}</span><div className="flex items-center gap-3">
                <label className="flex items-center gap-2 text-sm"><Checkbox checked={!settings.excludedNodes.includes(n.id)}
                  onCheckedChange={x => toggleNodeExclusion(n.id, x === true)} />Учитывать</label>
                <input aria-label={'Цвет ' + n.name} type="color" value={colorOf(n.id)}
                  onChange={e => setSettings(old => ({ ...old, colors: { ...old.colors, [n.id]: e.target.value } }))} />
              </div>
            </div>)}
          </CardContent></Card>
          <Card><CardHeader><CardTitle>Мостовые подписки</CardTitle></CardHeader><CardContent className="max-h-80 space-y-2 overflow-auto">
            {catalog.subscriptions.map(u => <div className="flex justify-between gap-3 border-b py-2" key={u.uuid}>
              <span className="text-sm">{u.uuid}</span><label className="flex items-center gap-2 text-sm">
                <Checkbox checked={!settings.excludedSubscriptions.includes(u.uuid)}
                  onCheckedChange={x => toggleSubscriptionExclusion(u.uuid, x === true)} />Учитывать
              </label>
            </div>)}
          </CardContent></Card>
          <p className="text-xs text-muted-foreground">Фильтры и цвета сохраняются в браузере, а не в базе панели. Сбор данных не меняется.</p>
        </TabsContent>
      </Tabs>
    </div>
  </div>
}
