import { Button } from '@/components/ui/button'
import { Textarea } from '@/components/ui/textarea'
import { AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle, AlertDialogTrigger } from '@/components/ui/alert-dialog'
import { useTranslation } from 'react-i18next'
import type { XrayInboundInfo, XrayStatus } from './node-xray-types'

type Props = {
  inbounds: XrayInboundInfo[]
  managedInboundTags?: string[]
  runtime: XrayStatus | undefined
  inboundTag: string
  onInboundTagChange: (tag: string) => void
  usersJson: string
  onUsersJsonChange: (value: string) => void
  busy: boolean
  onManageInbound: (tag: string, enabled: boolean) => void
  onSyncUsers: () => void
}

const supportsSubscriptionClients = (protocol: string) => ['vless', 'vmess', 'trojan'].includes(protocol)
const supportsManualClients = (protocol: string) => ['socks', 'vless', 'vmess', 'trojan'].includes(protocol)

export function NodeXrayClientManagement({ inbounds, managedInboundTags, runtime, inboundTag, onInboundTagChange,
  usersJson, onUsersJsonChange, busy, onManageInbound, onSyncUsers }: Props) {
  const { t } = useTranslation()
  const tr = (key: string, options?: Record<string, string | number>) =>
    t(`nodes.xrayClients.${key}`, options)
  // Missing ownership data must never look like an unmanaged inbound.
  const ownershipVerified = managedInboundTags !== undefined
  const managedTags = new Set(managedInboundTags ?? [])
  const isManaged = managedTags.has(inboundTag.trim())
  const manualInbounds = ownershipVerified
    ? inbounds.filter(inbound => !managedTags.has(inbound.tag) && supportsManualClients(inbound.protocol))
    : []
  const selectedManualInbound = manualInbounds.some(inbound => inbound.tag === inboundTag) ? inboundTag : ''
  const subscriptionInbounds = inbounds.filter(inbound => supportsSubscriptionClients(inbound.protocol))

  return <section className="min-w-0 space-y-3" aria-label={tr('sectionTitle')}>
    <div className="space-y-1">
      <h3 className="text-sm font-semibold">{tr('sectionTitle')}</h3>
      <p className="text-xs text-muted-foreground">{tr('sectionDescription')}</p>
    </div>
    {inbounds.length === 0 ? <p className="text-sm text-muted-foreground">{tr('empty')}</p> : (
      <div className="divide-y divide-border/60 overflow-hidden rounded-lg border border-border/60" data-testid="node-xray-inbound-list">
        {inbounds.map(inbound => {
          const managed = managedTags.has(inbound.tag)
          return <div key={inbound.tag} className="flex min-w-0 flex-wrap items-center justify-between gap-x-4 gap-y-2 px-3 py-3 sm:px-4">
            <div className="min-w-0 space-y-1">
              <p className="min-w-0 break-words text-sm font-medium">{inbound.tag}</p>
              <p className="text-xs text-muted-foreground">{inbound.protocol.toUpperCase()} · {inbound.port}</p>
            </div>
            <p className="text-xs text-muted-foreground">
              {!ownershipVerified ? tr('ownershipUnknownStatus') : managed ? tr('managedStatus') : tr('manualStatus', { count: inbound.user_count })}
            </p>
          </div>
        })}
      </div>
    )}

    <details className="min-w-0 rounded-md border border-dashed border-border/70" data-testid="node-xray-client-maintenance">
      <summary className="cursor-pointer px-3 py-3 text-sm font-medium focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring sm:px-4">
        {tr('maintenanceTitle')}
      </summary>
      <div className="space-y-4 border-t px-3 py-4 sm:px-4">
        <p className="text-xs text-muted-foreground">{tr('maintenanceWarning')}</p>
        {!ownershipVerified && <p role="status" className="text-xs text-muted-foreground">{tr('ownershipUnknownHelp')}</p>}
        {ownershipVerified && subscriptionInbounds.length > 0 && <div className="divide-y divide-border/60 rounded-md border">
          {subscriptionInbounds.map(inbound => {
            const managed = managedTags.has(inbound.tag)
            const enabled = !managed
            return <div key={inbound.tag} className="flex min-w-0 flex-wrap items-center justify-between gap-3 px-3 py-3">
              <div className="min-w-0 space-y-1">
                <p className="break-words text-sm font-medium">{inbound.tag}</p>
                <p className="text-xs text-muted-foreground">{managed ? tr('managedStatus') : tr('manualStatus', { count: inbound.user_count })}</p>
              </div>
              <AlertDialog>
                <AlertDialogTrigger asChild>
                  <Button type="button" size="sm" variant="outline" className="shrink-0 whitespace-normal"
                    disabled={busy || runtime?.runtime_status !== 'running'}>
                    {managed ? tr('disableAction') : tr('enableAction')}
                  </Button>
                </AlertDialogTrigger>
                <AlertDialogContent>
                  <AlertDialogHeader>
                    <AlertDialogTitle>{tr(managed ? 'disableConfirmTitle' : 'enableConfirmTitle')}</AlertDialogTitle>
                    <AlertDialogDescription className="space-y-2">
                      <span className="block break-all font-medium text-foreground">{inbound.tag}</span>
                      <span className="block">{tr(managed ? 'disableConfirmDescription' : 'enableConfirmDescription')}</span>
                      <span className="block">{tr('queuedNote')}</span>
                    </AlertDialogDescription>
                  </AlertDialogHeader>
                  <AlertDialogFooter>
                    <AlertDialogCancel>{tr('cancel')}</AlertDialogCancel>
                    <AlertDialogAction onClick={() => onManageInbound(inbound.tag, enabled)}
                      className={managed ? 'bg-destructive text-destructive-foreground hover:bg-destructive/90' : undefined}>
                      {tr(managed ? 'disableConfirmAction' : 'enableConfirmAction')}
                    </AlertDialogAction>
                  </AlertDialogFooter>
                </AlertDialogContent>
              </AlertDialog>
            </div>
          })}
        </div>}

        <div className="space-y-3 border-t pt-4">
          <div className="space-y-1">
            <h4 className="text-sm font-medium">{tr('manualSyncTitle')}</h4>
            <p className="text-xs text-muted-foreground">{tr('manualSyncDescription')}</p>
          </div>
          <select aria-label="Xray inbound tag" value={selectedManualInbound}
            onChange={event => onInboundTagChange(event.target.value)}
            className="border-input bg-background min-h-10 w-full min-w-0 rounded-md border px-3 text-sm"
            disabled={busy || !ownershipVerified || manualInbounds.length === 0}>
            <option value="">{tr('selectManualInbound')}</option>
            {manualInbounds.map(inbound => <option key={inbound.tag} value={inbound.tag}>{inbound.tag} · {inbound.protocol.toUpperCase()}</option>)}
          </select>
          <Textarea aria-label="Xray inbound users JSON" value={usersJson} rows={5}
            className="font-mono text-xs" placeholder={'[{"user":"qa","pass":"strong-password"}]'}
            onChange={event => onUsersJsonChange(event.target.value)} />
          <AlertDialog>
            <AlertDialogTrigger asChild>
              <Button type="button" size="sm" variant="outline"
                disabled={busy || !ownershipVerified || !usersJson.trim() || !selectedManualInbound || !runtime?.configured || isManaged}>
                {tr('syncAction')}
              </Button>
            </AlertDialogTrigger>
            <AlertDialogContent>
              <AlertDialogHeader>
                <AlertDialogTitle>{tr('manualConfirmTitle')}</AlertDialogTitle>
                <AlertDialogDescription className="space-y-2">
                  <span className="block break-all font-medium text-foreground">{selectedManualInbound}</span>
                  <span className="block">{tr('manualConfirmDescription')}</span>
                  <span className="block">{tr('queuedNote')}</span>
                </AlertDialogDescription>
              </AlertDialogHeader>
              <AlertDialogFooter>
                <AlertDialogCancel>{tr('cancel')}</AlertDialogCancel>
                <AlertDialogAction onClick={onSyncUsers}>{tr('manualConfirmAction')}</AlertDialogAction>
              </AlertDialogFooter>
            </AlertDialogContent>
          </AlertDialog>
        </div>
      </div>
    </details>
  </section>
}
