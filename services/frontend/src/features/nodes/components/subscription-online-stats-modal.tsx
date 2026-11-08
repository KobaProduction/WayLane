import { useQuery } from '@tanstack/react-query'
import { useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Activity, ArrowLeft, Eye, RefreshCw, Search } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Separator } from '@/components/ui/separator'
import { fetcher } from '@/service/http'

const UUID_V4 = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i
const ONLINE_REFRESH_MS = 10_000

type NodeOnlineStats = Record<string, number>
type SubscriptionOnlineIPs = { ips: Record<string, number> }

type SubscriptionOnlineStatsDialogProps = {
  isOpen: boolean
  onOpenChange: (open: boolean) => void
  nodeId: number | null
  nodeName?: string
}

export default function SubscriptionOnlineStatsDialog({ isOpen, onOpenChange, nodeId, nodeName }: SubscriptionOnlineStatsDialogProps) {
  const { t } = useTranslation()
  const [input, setInput] = useState('')
  const [subscriptionUuid, setSubscriptionUuid] = useState('')
  const [showIPs, setShowIPs] = useState(false)
  const validSelection = isOpen && nodeId != null && UUID_V4.test(subscriptionUuid)
  const base = `/api/node/${nodeId}/subscriptions/${subscriptionUuid}`

  const stats = useQuery({
    queryKey: ['node-subscription-online-stats', nodeId, subscriptionUuid],
    queryFn: () => fetcher<NodeOnlineStats>(`${base}/online-stats`),
    enabled: validSelection,
    refetchInterval: validSelection ? ONLINE_REFRESH_MS : false,
  })
  const ips = useQuery({
    queryKey: ['node-subscription-online-ips', nodeId, subscriptionUuid],
    queryFn: () => fetcher<SubscriptionOnlineIPs>(`${base}/online-ips`),
    enabled: validSelection && showIPs,
    refetchInterval: validSelection && showIPs ? ONLINE_REFRESH_MS : false,
  })
  const protocols = useMemo(
    () => Object.entries(stats.data ?? {}).filter(([, connections]) => connections > 0),
    [stats.data],
  )
  const activeIPs = useMemo(() => {
    const entries = Object.entries(ips.data?.ips ?? {})
    return { entries: entries.slice(0, 100), more: entries.length > 100 }
  }, [ips.data])

  const selectSubscription = () => {
    const uuid = input.trim().toLowerCase()
    if (!UUID_V4.test(uuid)) return
    setShowIPs(false)
    setSubscriptionUuid(uuid)
  }
  const close = (open: boolean) => {
    if (!open) {
      setInput('')
      setSubscriptionUuid('')
      setShowIPs(false)
    }
    onOpenChange(open)
  }
  const refresh = () => {
    if (showIPs) void ips.refetch()
    else void stats.refetch()
  }
  const formatLastSeen = (timestamp: number) => {
    if (!Number.isFinite(timestamp)) return '—'
    return new Date(timestamp < 1e12 ? timestamp * 1000 : timestamp).toLocaleString()
  }

  return (
    <Dialog open={isOpen} onOpenChange={close}>
      <DialogContent className="flex h-[90vh] max-w-full flex-col sm:h-[600px] sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2"><Activity className="size-5" />{t('subscriptionOnlineStats', { defaultValue: 'Subscription activity' })}</DialogTitle>
          {nodeName ? <p className="text-muted-foreground text-sm">{t('nodeModal.onlineStats.nodeInfo', { defaultValue: 'Node: {{nodeName}}', nodeName })}</p> : null}
        </DialogHeader>
        {!showIPs ? (
          <div className="flex flex-wrap items-center gap-2">
            <Input className="min-w-0 flex-1 font-mono" aria-label={t('subscriptionUuid')} placeholder={t('subscriptionUuid', { defaultValue: 'Subscription UUIDv4' })} value={input} onChange={event => setInput(event.target.value)} onKeyDown={event => { if (event.key === 'Enter') selectSubscription() }} />
            <Button type="button" size="icon" aria-label={t('search')} disabled={!UUID_V4.test(input.trim())} onClick={selectSubscription}><Search className="size-4" /></Button>
            <Button type="button" size="icon" variant="outline" aria-label={t('refresh')} disabled={!validSelection || stats.isFetching} onClick={refresh}><RefreshCw className="size-4" /></Button>
          </div>
        ) : (
          <Button type="button" variant="ghost" className="self-start" onClick={() => setShowIPs(false)}><ArrowLeft className="mr-2 size-4" />{t('nodeModal.onlineStats.backToStats', { defaultValue: 'Back to stats' })}</Button>
        )}
        <Separator />
        <ScrollArea className="min-h-0 flex-1">
          {!validSelection ? <p className="text-muted-foreground p-4 text-center text-sm">{t('subscriptionSearchByUuid', { defaultValue: 'Enter a subscription UUIDv4 to inspect node activity.' })}</p> : showIPs ? (
            ips.isPending ? <p className="text-muted-foreground p-4">{t('loading')}</p> : ips.isError ? <p role="alert" className="text-destructive p-4">{t('subscriptionRequestFailed')}</p> : (
              <div className="space-y-2 p-2">
                {activeIPs.entries.length === 0 ? <p className="text-muted-foreground p-3">{t('subscriptionNoDevices', { defaultValue: 'No recent connections' })}</p> : null}
                {activeIPs.entries.map(([ip, timestamp]) => (
                  <div key={ip} className="bg-accent/40 rounded-lg p-3 text-sm"><p className="font-mono break-all">{ip}</p><p className="text-muted-foreground text-xs">{formatLastSeen(timestamp)}</p></div>
                ))}
                {activeIPs.more ? <p className="text-muted-foreground text-xs">{t('nodeModal.onlineStats.showingFirst', { defaultValue: 'Showing first {{count}} IP addresses', count: 100 })}</p> : null}
              </div>
            )
          ) : stats.isPending ? <p className="text-muted-foreground p-4">{t('loading')}</p> : stats.isError ? (
            <p role="alert" className="text-destructive p-4">{t('subscriptionRequestFailed')}</p>
          ) : protocols.length === 0 ? (
            <p className="text-muted-foreground p-4">{t('nodeModal.onlineStats.userNotOnline', { defaultValue: 'No active connections on this node' })}</p>
          ) : (
            <Card>
              <CardHeader><CardTitle className="font-mono text-sm break-all">{subscriptionUuid}</CardTitle></CardHeader>
              <CardContent className="space-y-4">
                <div className="flex flex-wrap gap-2">{protocols.map(([protocol, connections]) => <Badge key={protocol} variant="outline">{protocol}: {connections}</Badge>)}</div>
                <Button type="button" variant="outline" size="sm" onClick={() => setShowIPs(true)}><Eye className="mr-2 size-4" />{t('nodeModal.onlineStats.viewIPs', { defaultValue: 'View IPs' })}</Button>
              </CardContent>
            </Card>
          )}
        </ScrollArea>
        {validSelection ? <p className="text-muted-foreground border-t pt-2 text-center text-xs">{t('nodeModal.onlineStats.autoRefresh', { defaultValue: 'Auto-refreshing every 10 seconds' })}</p> : null}
      </DialogContent>
    </Dialog>
  )
}
