import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useCallback, useState, type FormEvent } from 'react'
import { useTranslation } from 'react-i18next'
import { Copy, Fingerprint, KeyRound, Plus, RefreshCw, Trash2 } from 'lucide-react'
import { toast } from 'sonner'
import PageHeader from '@/components/layout/page-header'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Badge } from '@/components/ui/badge'
import { Separator } from '@/components/ui/separator'
import { Checkbox } from '@/components/ui/checkbox'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle } from '@/components/ui/alert-dialog'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { fetcher } from '@/service/http'
import { useAdmin } from '@/hooks/use-admin'
import { useCommandCreate } from '@/hooks/use-command-create'
import { hasPermission } from '@/utils/rbac'

type Entitlement = 'active' | 'payment_required' | 'suspended' | 'expired' | 'cancelled'

type Subscription = {
  uuid: string
  managed: boolean
  entitlement: Entitlement
  entitlement_revision: number
  technical_status: string | null
  expire_at: string | null
  traffic_limit: number | null
  device_limit: number | null
}
type SubscriptionList = { items: Subscription[]; total: number; limit: number; offset: number }
type Groups = { groups: { id: number; name: string; is_disabled: boolean }[]; total: number }
type SubscriptionLink = { subscription_uuid: string; url: string }
type Device = { hwid: string; device_os: string | null; os_version: string | null; device_model: string | null; created_at: string; last_used_at: string }
type DeviceList = { devices: Device[]; count: number }
type BatchAction = 'disable' | 'enable' | 'reset_usage' | 'delete' | 'reissue'
type BatchResult = { uuids: string[]; count: number }

type PendingAction =
  | { kind: 'rotate'; subscription: Subscription }
  | { kind: 'device-delete'; subscription: Subscription; hwid: string }
  | { kind: 'devices-reset'; subscription: Subscription }
  | { kind: 'batch'; action: BatchAction; uuids: string[] }
  | null

export default function SubscriptionsPage() {
  const { t } = useTranslation()
  const { admin } = useAdmin()
  const queryClient = useQueryClient()
  const [offset, setOffset] = useState(0)
  const [creating, setCreating] = useState(false)
  const [groupId, setGroupId] = useState('')
  const [deviceLimit, setDeviceLimit] = useState('')
  const [expiresAt, setExpiresAt] = useState('')
  const [pending, setPending] = useState<PendingAction>(null)
  const [selectedUuids, setSelectedUuids] = useState<string[]>([])
  const [copying, setCopying] = useState<string | null>(null)
  const [deviceSubscription, setDeviceSubscription] = useState<Subscription | null>(null)
  const canReadDevices = hasPermission(admin, 'hwids', 'read')
  const canManageDevices = hasPermission(admin, 'hwids', 'delete')
  const canCreate = hasPermission(admin, 'subscriptions', 'create')
  const canUpdate = hasPermission(admin, 'subscriptions', 'update')
  const canDelete = hasPermission(admin, 'subscriptions', 'delete')
  const canResetUsage = hasPermission(admin, 'subscriptions', 'reset_usage')
  const canReissue = hasPermission(admin, 'subscriptions', 'revoke_sub')
  const canBulk = canUpdate || canDelete || canResetUsage || canReissue
  const pageSize = 30
  const batchLabels: Record<BatchAction, string> = {
    disable: t('subscriptionBatchDisable', { defaultValue: 'Disable VPN' }),
    enable: t('subscriptionBatchEnable', { defaultValue: 'Enable VPN' }),
    reset_usage: t('subscriptionBatchResetUsage', { defaultValue: 'Reset traffic' }),
    reissue: t('subscriptionBatchReissue', { defaultValue: 'Reissue VPN keys' }),
    delete: t('subscriptionBatchDelete', { defaultValue: 'Delete subscriptions' }),
  }

  const list = useQuery({
    queryKey: ['waylane-product-subscriptions', offset],
    queryFn: () => fetcher<SubscriptionList>(`/api/subscriptions?limit=${pageSize}&offset=${offset}`),
  })
  const selectedHasCoreManaged = selectedUuids.some(uuid =>
    list.data?.items.some(item => item.uuid === uuid && item.managed)
  )
  const groups = useQuery({
    queryKey: ['waylane-subscription-groups'],
    queryFn: () => fetcher<Groups>('/api/groups?limit=100&offset=0'),
    enabled: creating && canCreate,
  })
  const devices = useQuery({
    queryKey: ['waylane-product-subscription-devices', deviceSubscription?.uuid],
    queryFn: () => fetcher<DeviceList>(`/api/subscriptions/${deviceSubscription!.uuid}/devices`),
    enabled: !!deviceSubscription && canReadDevices,
  })
  const refresh = () => queryClient.invalidateQueries({ queryKey: ['waylane-product-subscriptions'] })
  const refreshDevices = () => queryClient.invalidateQueries({ queryKey: ['waylane-product-subscription-devices'] })
  const failure = () => toast.error(t('subscriptionRequestFailed'))
  const create = useMutation({
    mutationFn: (data: { group_ids: number[]; device_limit?: number; expire_at?: string }) =>
      fetcher<Subscription>('/api/subscriptions', { method: 'POST', body: data }),
    onSuccess: () => {
      toast.success(t('subscriptionCreated'))
      setCreating(false)
      setGroupId('')
      setDeviceLimit('')
      setExpiresAt('')
      setOffset(0)
      void refresh()
    },
    onError: failure,
  })
  const rotate = useMutation({
    mutationFn: (item: Subscription) => fetcher<SubscriptionLink>(`/api/subscriptions/${item.uuid}/rotate-access`, { method: 'POST' }),
    onSuccess: () => {
      toast.success(t('subscriptionCodeRotated'))
      void refresh()
    },
    onError: failure,
  })

  const deleteDevice = useMutation({
    mutationFn: ({ subscription, hwid }: { subscription: Subscription; hwid: string }) =>
      fetcher(`/api/subscriptions/${subscription.uuid}/devices/${encodeURIComponent(hwid)}`, { method: 'DELETE' }),
    onSuccess: () => {
      toast.success(t('subscriptionDeviceDeleted'))
      void refreshDevices()
    },
    onError: failure,
  })
  const resetDevices = useMutation({
    mutationFn: (subscription: Subscription) =>
      fetcher(`/api/subscriptions/${subscription.uuid}/devices/reset`, { method: 'POST' }),
    onSuccess: () => {
      toast.success(t('subscriptionDevicesReset'))
      void refreshDevices()
    },
    onError: failure,
  })

  const batch = useMutation({
    mutationFn: ({ action, uuids }: { action: BatchAction; uuids: string[] }) =>
      fetcher<BatchResult>(`/api/subscriptions/bulk/${action}`, { method: 'POST', body: { uuids } }),
    onSuccess: result => {
      toast.success(t('operationSuccess', { defaultValue: 'Operation successful' }), {
        description: t('subscriptionBatchAffected', { count: result.count, defaultValue: '{{count}} subscriptions updated' }),
      })
      setSelectedUuids([])
      void refresh()
    },
    onError: failure,
  })
  const toggleSelection = (uuid: string, checked: boolean) => {
    setSelectedUuids(previous => checked ? [...previous.filter(id => id !== uuid), uuid] : previous.filter(id => id !== uuid))
  }

  const openCreate = useCallback(() => {
    if (canCreate) setCreating(true)
  }, [canCreate])
  useCommandCreate('subscription', openCreate)

  const confirm = () => {
    if (!pending) return
    if (pending.kind === 'rotate') rotate.mutate(pending.subscription)
    else if (pending.kind === 'device-delete') deleteDevice.mutate({ subscription: pending.subscription, hwid: pending.hwid })
    else if (pending.kind === 'devices-reset') resetDevices.mutate(pending.subscription)
    else if (pending.kind === 'batch') batch.mutate({ action: pending.action, uuids: pending.uuids })
    setPending(null)
  }
  const copyLink = async (item: Subscription) => {
    setCopying(item.uuid)
    try {
      const link = await fetcher<SubscriptionLink>(`/api/subscriptions/${item.uuid}/link`)
      await navigator.clipboard.writeText(link.url)
      toast.success(t('subscriptionLinkCopied'))
    } catch {
      failure()
    } finally {
      setCopying(null)
    }
  }
  const copyUuid = async (uuid: string) => {
    try {
      await navigator.clipboard.writeText(uuid)
      toast.success(t('subscriptionUuidCopied'))
    } catch {
      failure()
    }
  }
  const submitCreate = (event: FormEvent) => {
    event.preventDefault()
    if (!groupId) return
    const expires = expiresAt ? new Date(expiresAt).toISOString() : undefined
    create.mutate({
      group_ids: [Number(groupId)],
      device_limit: deviceLimit ? Number(deviceLimit) : undefined,
      expire_at: expires,
    })
  }

  return (
    <div className="flex w-full flex-col gap-3">
      <PageHeader title="subscriptions" description="subscriptionsDescription" buttonText={canCreate ? 'createSubscription' : undefined} buttonIcon={Plus} onButtonClick={canCreate ? openCreate : undefined} />
      <Separator />
      <div className="space-y-3 px-4 pb-6">
        <div className="flex items-center justify-between gap-3">
          <p className="text-muted-foreground text-sm">{list.data ? t('subscriptionRecords', { count: list.data.total }) : t('subscriptionsDescription')}</p>
          <Button type="button" variant="outline" size="sm" onClick={() => void list.refetch()} disabled={list.isFetching}>
            <RefreshCw className="mr-2 size-4" />{t('refresh')}
          </Button>
        </div>
        {canBulk && list.data?.items.length ? (
          <div className="flex flex-wrap items-center gap-2 rounded-lg border p-3">
            <span className="text-muted-foreground text-sm">{t('bulk.selectedCount', { count: selectedUuids.length, defaultValue: '{{count}} selected' })}</span>
            <Button type="button" variant="outline" size="sm" disabled={batch.isPending} onClick={() => setSelectedUuids(selectedUuids.length === list.data!.items.length ? [] : list.data!.items.map(item => item.uuid))}>
              {selectedUuids.length === list.data.items.length ? t('deselectAll') : t('subscriptionSelectPage', { defaultValue: 'Select page' })}
            </Button>
            {canUpdate && !selectedHasCoreManaged ? <>
              <Button type="button" variant="outline" size="sm" disabled={!selectedUuids.length || batch.isPending} onClick={() => setPending({ kind: 'batch', action: 'disable', uuids: [...selectedUuids] })}>{t('bulk.disable', { defaultValue: 'Disable VPN' })}</Button>
              <Button type="button" variant="outline" size="sm" disabled={!selectedUuids.length || batch.isPending} onClick={() => setPending({ kind: 'batch', action: 'enable', uuids: [...selectedUuids] })}>{t('bulk.enable', { defaultValue: 'Enable VPN' })}</Button>
            </> : null}
            {canResetUsage && !selectedHasCoreManaged ? <Button type="button" variant="outline" size="sm" disabled={!selectedUuids.length || batch.isPending} onClick={() => setPending({ kind: 'batch', action: 'reset_usage', uuids: [...selectedUuids] })}>{t('bulk.resetUsage', { defaultValue: 'Reset usage' })}</Button> : null}
            {canReissue ? <Button type="button" variant="outline" size="sm" disabled={!selectedUuids.length || batch.isPending} onClick={() => setPending({ kind: 'batch', action: 'reissue', uuids: [...selectedUuids] })}>{t('subscriptionRotateCode', { defaultValue: 'Reissue VPN keys' })}</Button> : null}
            {selectedHasCoreManaged ? <span className="text-muted-foreground text-xs">{t('subscriptionCoreOwnedPolicy', { defaultValue: 'Technical policy and usage of Core-managed subscriptions are controlled in VPN Core' })}</span> : null}
            {canDelete ? <Button type="button" variant="destructive" size="sm" disabled={!selectedUuids.length || batch.isPending} onClick={() => setPending({ kind: 'batch', action: 'delete', uuids: [...selectedUuids] })}>{t('delete', { defaultValue: 'Delete' })}</Button> : null}
          </div>
        ) : null}
        {list.isPending ? <p className="text-muted-foreground text-sm">{t('loading')}</p> : null}
        {list.isError ? <p role="alert" className="text-destructive text-sm">{t('subscriptionRequestFailed')}</p> : null}
        {list.data?.items.length === 0 ? <div className="text-muted-foreground rounded-lg border p-6 text-center text-sm">{t('subscriptionEmpty')}</div> : null}
        <div className="grid gap-3 xl:grid-cols-2">
          {list.data?.items.map(item => (
            <article key={item.uuid} className="bg-card space-y-3 rounded-lg border p-4 shadow-xs">
              <div className="flex flex-wrap items-start justify-between gap-2">
                {canBulk ? <Checkbox checked={selectedUuids.includes(item.uuid)} onCheckedChange={checked => toggleSelection(item.uuid, checked === true)} aria-label={`${t('subscriptions')} ${item.uuid}`} /> : null}
                <div className="min-w-0 flex-1">
                  <p className="text-muted-foreground mb-1 text-xs">{t('subscriptionUuid')}</p>
                  <div className="flex items-center gap-2">
                    <code className="min-w-0 break-all text-xs sm:text-sm">{item.uuid}</code>
                    <Button aria-label={t('subscriptionCopyUuid')} size="icon" variant="ghost" className="shrink-0" onClick={() => void copyUuid(item.uuid)}><Copy className="size-4" /></Button>
                  </div>
                </div>
                <Badge variant={item.entitlement === 'active' ? 'default' : 'secondary'}>{t(`subscriptionState_${item.entitlement}`)}</Badge>
              </div>
              <dl className="grid grid-cols-2 gap-3 text-sm sm:grid-cols-3">
                <div><dt className="text-muted-foreground text-xs">{t('subscriptionExpiry')}</dt><dd>{item.expire_at ? new Date(item.expire_at).toLocaleDateString() : t('unlimited')}</dd></div>
                <div><dt className="text-muted-foreground text-xs">{t('subscriptionDevices')}</dt><dd>{item.device_limit ?? t('unlimited')}</dd></div>
                <div><dt className="text-muted-foreground text-xs">{t('subscriptionTraffic')}</dt><dd>{item.traffic_limit ? `${(item.traffic_limit / (1024 ** 3)).toFixed(2)} GB` : t('unlimited')}</dd></div>
                <div><dt className="text-muted-foreground text-xs">{t('subscriptionTechnicalStatus', { defaultValue: 'Technical status' })}</dt><dd>{item.technical_status || '—'}</dd></div>
              </dl>
              <div className="flex flex-wrap items-center gap-2 border-t pt-3">
                {canReadDevices ? <Button type="button" variant="outline" size="sm" onClick={() => setDeviceSubscription(item)}>
                  <Fingerprint className="mr-2 size-4" />{t('subscriptionManageDevices')}
                </Button> : null}
                <Button type="button" variant="outline" size="sm" onClick={() => void copyLink(item)} disabled={copying === item.uuid}>
                  <Copy className="mr-2 size-4" />{t('subscriptionCopyLink')}
                </Button>
                {canUpdate ? <>
                  <Button type="button" variant="outline" size="sm" onClick={() => setPending({ kind: 'rotate', subscription: item })}>
                    <KeyRound className="mr-2 size-4" />{t('subscriptionRotateCode')}
                  </Button>
                </> : null}
              </div>
            </article>
          ))}
        </div>
        {list.data && list.data.total > pageSize ? (
          <nav className="flex items-center justify-between gap-3" aria-label={t('subscriptions')}>
            <Button type="button" variant="outline" disabled={offset === 0} onClick={() => { setOffset(Math.max(0, offset - pageSize)); setSelectedUuids([]) }}>{t('previous')}</Button>
            <span className="text-muted-foreground text-sm">{Math.min(offset + 1, list.data.total)}–{Math.min(offset + pageSize, list.data.total)} / {list.data.total}</span>
            <Button type="button" variant="outline" disabled={offset + pageSize >= list.data.total} onClick={() => { setOffset(offset + pageSize); setSelectedUuids([]) }}>{t('next')}</Button>
          </nav>
        ) : null}
      </div>
      <Dialog open={creating} onOpenChange={setCreating}>
        <DialogContent>
          <DialogHeader><DialogTitle>{t('createSubscription')}</DialogTitle><DialogDescription>{t('subscriptionCreateDescription')}</DialogDescription></DialogHeader>
          <form className="space-y-4" onSubmit={submitCreate}>
            <label className="block space-y-1 text-sm">
              <span>{t('subscriptionGroup')}</span>
              <Select value={groupId} onValueChange={setGroupId}>
                <SelectTrigger><SelectValue placeholder={t('subscriptionSelectGroup')} /></SelectTrigger>
                <SelectContent>{groups.data?.groups.filter(group => !group.is_disabled).map(group => <SelectItem key={group.id} value={String(group.id)}>{group.name}</SelectItem>)}</SelectContent>
              </Select>
            </label>
            <label className="block space-y-1 text-sm"><span>{t('subscriptionDevices')}</span><Input inputMode="numeric" type="number" min={1} value={deviceLimit} onChange={event => setDeviceLimit(event.target.value)} placeholder={t('unlimited')} /></label>
            <label className="block space-y-1 text-sm"><span>{t('subscriptionExpiry')}</span><Input type="datetime-local" value={expiresAt} onChange={event => setExpiresAt(event.target.value)} /></label>
            <p className="text-muted-foreground text-xs">{t('subscriptionCreatedInactive')}</p>
            <DialogFooter><Button type="button" variant="outline" onClick={() => setCreating(false)}>{t('cancel')}</Button><Button type="submit" disabled={!groupId || create.isPending}>{t('createSubscription')}</Button></DialogFooter>
          </form>
        </DialogContent>
      </Dialog>
      <Dialog open={deviceSubscription !== null} onOpenChange={open => { if (!open) setDeviceSubscription(null) }}>
        <DialogContent className="max-h-[85vh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle>{t('subscriptionManageDevices')}</DialogTitle>
            <DialogDescription>{deviceSubscription?.uuid}</DialogDescription>
          </DialogHeader>
          {devices.isPending ? <p className="text-muted-foreground text-sm">{t('loading')}</p> : null}
          {devices.isError ? <p role="alert" className="text-destructive text-sm">{t('subscriptionRequestFailed')}</p> : null}
          {devices.data?.devices.length === 0 ? <p className="text-muted-foreground text-sm">{t('subscriptionNoDevices')}</p> : null}
          <div className="space-y-2">
            {devices.data?.devices.map(device => <div key={device.hwid} className="flex items-start justify-between gap-3 rounded-lg border p-3">
              <div className="min-w-0 space-y-1 text-sm">
                <div className="font-medium break-words">{device.device_model || device.device_os || t('subscriptionUnknownDevice')}</div>
                <p className="text-muted-foreground break-all font-mono text-xs">{device.hwid}</p>
                <p className="text-muted-foreground text-xs">{device.device_os || ''} {device.os_version || ''}</p>
              </div>
              {canManageDevices && deviceSubscription ? <Button type="button" size="icon" variant="ghost" aria-label={t('subscriptionDeleteDevice')} onClick={() => setPending({ kind: 'device-delete', subscription: deviceSubscription, hwid: device.hwid })}><Trash2 className="size-4" /></Button> : null}
            </div>)}
          </div>
          {canManageDevices && deviceSubscription && !!devices.data?.count ? <DialogFooter>
            <Button type="button" variant="destructive" onClick={() => setPending({ kind: 'devices-reset', subscription: deviceSubscription })}>{t('subscriptionResetDevices')}</Button>
          </DialogFooter> : null}
        </DialogContent>
      </Dialog>
      <AlertDialog open={pending !== null} onOpenChange={open => { if (!open) setPending(null) }}>
        <AlertDialogContent>
          <AlertDialogHeader><AlertDialogTitle>{pending?.kind === 'batch' ? t('bulk.reviewAndApply', { defaultValue: 'Confirm bulk operation' }) : pending?.kind === 'rotate' ? t('subscriptionRotateCode') : t('subscriptionManageDevices')}</AlertDialogTitle>
            <AlertDialogDescription>{pending?.kind === 'batch' ? `${pending.uuids.length} ${t('subscriptions')}: ${batchLabels[pending.action]}. ${t('subscriptionBulkWarning', { defaultValue: 'Commercial access remains controlled by VPN Core.' })}` : pending?.kind === 'rotate' ? t('subscriptionRotateWarning') : t('subscriptionDeviceWarning')}</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter><AlertDialogCancel>{t('cancel')}</AlertDialogCancel><AlertDialogAction onClick={confirm}>{t('confirm')}</AlertDialogAction></AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}
