import { useCallback, useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Link, useSearchParams } from 'react-router'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { $fetch } from '@/service/http'
import { useAdmin } from '@/hooks/use-admin'
import { hasPermission } from '@/utils/rbac'
import { filterByRegistration, registrationCounts, validRegistrationFilter } from './node-registry-overview'
import { observedWssPresence } from './node-wss-presence'
import type { NodeRegistration, NodeWssObservation, NodeWssSummary } from './node-registry-types'

/** Registration list is a read-only overview; all management actions live in the Node detail route. */
export default function NodeRegistryList() {
  const { t } = useTranslation()
  const { admin } = useAdmin()
  const canRead = hasPermission(admin, 'nodes', 'read')
  const canCreate = hasPermission(admin, 'nodes', 'create')
  const canReadStats = hasPermission(admin, 'nodes', 'stats')
  const [params, setParams] = useSearchParams()
  const filter = validRegistrationFilter(params.get('status'))
  const [nodes, setNodes] = useState<NodeRegistration[]>([])
  const [observations, setObservations] = useState<NodeWssObservation[] | null>(null)
  const [checkedAt, setCheckedAt] = useState<number | null>(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)
  const [loaded, setLoaded] = useState(false)
  const generation = useRef(0)

  const refresh = useCallback(async () => {
    if (!canRead) return
    const current = ++generation.current
    setLoading(true)
    try {
      const result = await $fetch<{ nodes: NodeRegistration[] }>('/api/node-registry/nodes')
      if (current !== generation.current) return
      setNodes(result.nodes)
      setError('')
      setObservations(null)
      setCheckedAt(null)
      // Analytics read permission is separate; absence means unknown, not disconnected.
      if (canReadStats) {
        try {
          const snapshot = await $fetch<NodeWssSummary>('/api/node-registry/analytics/summary')
          if (current === generation.current) {
            setObservations(snapshot.nodes)
            setCheckedAt(Date.now())
          }
        } catch { /* Registration data remains valid even when WSS analytics is unavailable. */ }
      }
    } catch (reason) {
      if (current === generation.current) setError(reason instanceof Error ? reason.message : 'Не удалось получить реестр Node')
    } finally {
      if (current === generation.current) {
        setLoaded(true)
        setLoading(false)
      }
    }
  }, [canRead, canReadStats])

  useEffect(() => {
    void refresh()
    const onRegistered = () => { void refresh() }
    window.addEventListener('waylane:nodes-refresh', onRegistered)
    return () => {
      generation.current++
      window.removeEventListener('waylane:nodes-refresh', onRegistered)
    }
  }, [refresh])

  if (!canRead) return <p className="p-4 text-sm text-muted-foreground" role="alert">{t('nodes.details.noAccess', { defaultValue: 'Недостаточно прав для просмотра Node.' })}</p>

  const counts = registrationCounts(nodes)
  const visible = filterByRegistration(nodes, filter)
  const checkedLabel = checkedAt !== null ? new Date(checkedAt).toLocaleTimeString() : null
  return (
    <section className="min-w-0 space-y-4 px-4 py-4" aria-label={t('nodes.registryOverview.title', { defaultValue: 'Реестр узлов' })}>
      <header className="flex flex-wrap items-center justify-between gap-3">
        <div className="space-y-1">
          <h2 className="text-lg font-semibold">{t('nodes.registryOverview.title', { defaultValue: 'Реестр узлов' })}</h2>
          <p className="text-sm text-muted-foreground">{t('nodes.registryOverview.description', { defaultValue: 'Регистрация и последний отчёт Agent. Подтверждение не означает подключение.' })}</p>
        </div>
        <Button type="button" variant="outline" size="sm" onClick={() => void refresh()} disabled={loading}>
          {t('nodes.registryOverview.refresh', { defaultValue: 'Обновить список' })}
        </Button>
      </header>
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4" aria-label={t('nodes.registryOverview.stats', { defaultValue: 'Статистика регистрации' })}>
        {(['all', 'approved', 'pending', 'revoked'] as const).map(status => (
          <button type="button" key={status} aria-pressed={filter === status}
            className={`rounded-lg border px-3 py-3 text-left transition-colors focus-visible:ring-2 focus-visible:ring-ring ${filter === status ? 'border-primary bg-primary/5' : 'hover:bg-muted/40'}`}
            onClick={() => {
              const next = new URLSearchParams(params)
              if (status === 'all') next.delete('status')
              else next.set('status', status)
              setParams(next, { replace: true })
            }}>
            <span className="block text-xl font-semibold tabular-nums">{counts[status]}</span>
            <span className="text-xs text-muted-foreground">{t(`nodes.registryOverview.${status}`, { defaultValue: status })}</span>
          </button>
        ))}
      </div>
      {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
      {!loaded && !error && <p role="status" className="text-sm text-muted-foreground">{t('nodes.details.loading', { defaultValue: 'Загрузка данных Node…' })}</p>}
      {loaded && !error && nodes.length === 0 && <div className="space-y-3 rounded-lg border border-dashed p-8 text-center">
        <p className="text-sm text-muted-foreground">{t('nodes.emptyHint')}</p>
        {canCreate && <Button variant="outline" onClick={() => window.dispatchEvent(new Event('openNodeDialog'))}>{t('nodes.addNode')}</Button>}
      </div>}
      {loaded && !error && nodes.length > 0 && visible.length === 0 && (
        <p className="rounded-lg border border-dashed p-8 text-center text-sm text-muted-foreground" role="status">
          {t('nodes.registryOverview.noMatches', { defaultValue: 'Узлов с таким статусом нет.' })}
        </p>
      )}
      <div className="grid gap-3 lg:grid-cols-2">
        {visible.map(node => {
          const wss = observedWssPresence(node.node_uuid, checkedAt, observations)
          return <article key={node.node_uuid} className="min-w-0 space-y-3 rounded-xl border bg-card p-4">
            <div className="flex flex-wrap items-center gap-2">
              <Link className="min-w-0 break-words font-semibold text-primary hover:underline"
                to={`/nodes/registry/${encodeURIComponent(node.node_uuid)}${filter === 'all' ? '' : `?returnStatus=${filter}`}`}>
                {node.display_name}
              </Link>
              <Badge variant={node.status === 'approved' ? 'outline' : node.status === 'pending' ? 'yellow' : 'red'}>
                {t(`nodes.nodeStatus.${node.status}`, { defaultValue: node.status })}
              </Badge>
              <Badge variant="secondary">{node.connection_mode === 'classic' ? 'Classic' : 'Discovery'}</Badge>
            </div>
            <p className="text-sm text-muted-foreground">
              {node.last_seen_at ? `${t('nodes.lastSeen')}: ${new Date(node.last_seen_at).toLocaleString()}` : t('nodes.awaitingFirstHeartbeat')}
              {' · '}{t('nodes.agentVersion')}: {node.node_version}
            </p>
            <div className="flex flex-wrap items-center justify-between gap-2 border-t pt-3">
              <span className="text-xs text-muted-foreground" aria-label="WSS: наблюдение">
                {wss === 'unknown' ? t('nodes.registryOverview.wssUnknown', { defaultValue: 'WSS не проверен' }) :
                  wss === 'recent' ? t('nodes.registryOverview.wssRecent', { defaultValue: 'WSS-сигнал за 90 секунд до проверки' }) :
                    t('nodes.registryOverview.wssStale', { defaultValue: 'Нет свежего WSS-сигнала при проверке' })}
                {checkedLabel && wss !== 'unknown' ? ` · ${checkedLabel}` : ''}
              </span>
              <Link className="text-sm font-medium text-primary underline underline-offset-4"
                to={`/nodes/registry/${encodeURIComponent(node.node_uuid)}${filter === 'all' ? '' : `?returnStatus=${filter}`}`}>
                {t('nodes.details.open', { defaultValue: 'Открыть узел' })}
              </Link>
            </div>
          </article>
        })}
      </div>
    </section>
  )
}
