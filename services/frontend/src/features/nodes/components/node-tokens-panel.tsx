import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'
import { CheckCircle2, ClipboardCopy, Github, KeyRound, RefreshCw, ShieldCheck, ShieldOff } from 'lucide-react'
import { $fetch } from '@/service/http'
import { useAdmin } from '@/hooks/use-admin'
import { hasPermission } from '@/utils/rbac'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Checkbox } from '@/components/ui/checkbox'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'

export type DiscoveryInvitation = {
  id: number
  label: string
  created_at: string
  expires_at: string | null
  revoked: '0' | '1'
  auto_approve: '0' | '1'
}

type SharedGHCR = {
  repository: string
  configured: boolean
  username: string
  updated_at: string | null
}

const MAX_LIFETIME_HOURS = 8760

export default function NodeTokensPanel() {
  const { t, i18n } = useTranslation()
  const { admin } = useAdmin()
  const canRead = hasPermission(admin, 'nodes', 'read')
  const canCreate = hasPermission(admin, 'nodes', 'create')
  const canUpdate = hasPermission(admin, 'nodes', 'update')
  const [invitations, setInvitations] = useState<DiscoveryInvitation[]>([])
  const [ghcr, setGhcr] = useState<SharedGHCR | null>(null)
  const [login, setLogin] = useState('')
  const [pat, setPat] = useState('')
  const [label, setLabel] = useState('')
  const [autoApprove, setAutoApprove] = useState(false)
  const [lifetime, setLifetime] = useState('168')
  const [oneTime, setOneTime] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [editId, setEditId] = useState<number | null>(null)
  const [editLabel, setEditLabel] = useState('')

  const load = async () => {
    if (!canRead) return
    try {
      const [invitationData, shared] = await Promise.all([
        $fetch<{ invitations: DiscoveryInvitation[] }>('/api/node-registry/discovery-tokens'),
        $fetch<SharedGHCR>('/api/node-registry/tokens/ghcr'),
      ])
      setInvitations(invitationData.invitations)
      setGhcr(shared)
      setLogin(shared.username)
      setError('')
    } catch {
      setError(t('nodes.tokens.loadError'))
    }
  }

  useEffect(() => { void load() }, [canRead])

  const call = async (task: () => Promise<void>, message: string) => {
    if (busy) return
    setBusy(true)
    setError('')
    try {
      await task()
      toast.success(message)
    } catch {
      setError(t('nodes.tokens.actionError'))
    } finally { setBusy(false) }
  }

  const lifetimeHours = (): number | null => {
    const parsed = Number(lifetime)
    return Number.isInteger(parsed) && parsed >= 1 && parsed <= MAX_LIFETIME_HOURS ? parsed : null
  }
  const copyOneTime = async () => {
    try {
      await navigator.clipboard.writeText('DISCOVERY_TOKEN=' + oneTime)
      toast.success(t('nodes.tokenCopied'))
    } catch { toast.error(t('nodes.copyUnavailable')) }
  }

  const createInvitation = async () => {
    if (!label.trim()) return
    await call(async () => {
      const response = await $fetch<{ discovery_token: string }>('/api/node-registry/discovery-tokens', {
        method: 'POST', body: { label: label.trim(), auto_approve: autoApprove, lifetime_hours: lifetimeHours() },
      })
      setOneTime(response.discovery_token)
      setLabel('')
      await load()
    }, t('nodes.invitationCreated'))
  }

  const patchInvitation = async (id: number, patch: Record<string, unknown>) => {
    await call(async () => {
      await $fetch('/api/node-registry/discovery-tokens/' + id, { method: 'PATCH', body: patch })
      setEditId(null)
      await load()
    }, t('nodes.tokens.settingsSaved'))
  }

  const rotateInvitation = async (invitation: DiscoveryInvitation) => {
    if (!window.confirm(t('nodes.tokens.rotateConfirm', { name: invitation.label }))) return
    await call(async () => {
      const data = await $fetch<{ discovery_token: string }>(
        '/api/node-registry/discovery-tokens/' + invitation.id + '/rotate', {
          method: 'POST', body: { lifetime_hours: lifetimeHours() ?? 0 },
        },
      )
      setOneTime(data.discovery_token)
      await load()
    }, t('nodes.tokens.rotated'))
  }

  const saveGHCR = async () => {
    if (!login.trim() || !pat || busy) return
    await call(async () => {
      const updated = await $fetch<SharedGHCR>('/api/node-registry/tokens/ghcr', {
        method: 'PUT', body: { username: login.trim(), token: pat },
      })
      setGhcr(updated)
      setLogin(updated.username)
      setPat('')
    }, t('nodes.tokens.ghcrSaved'))
  }

  const deleteGHCR = async () => {
    if (!window.confirm(t('nodes.tokens.removeGHCRConfirm'))) return
    await call(async () => {
      const cleared = await $fetch<SharedGHCR>('/api/node-registry/tokens/ghcr', { method: 'DELETE' })
      setGhcr(cleared)
      setLogin('')
      setPat('')
    }, t('nodes.tokens.ghcrRemoved'))
  }

  if (!canRead) return <p className="p-6 text-muted-foreground">{t('nodes.logs.noAccess')}</p>

  return (
    <div className="grid min-w-0 gap-4 p-4 lg:grid-cols-2 lg:items-start">
      <Card className="min-w-0 shadow-sm">
        <CardHeader className="gap-2">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div className="flex items-center gap-2"><KeyRound className="h-5 w-5" /><CardTitle>{t('nodes.tokens.discoveryTitle')}</CardTitle></div>
            <Button size="sm" variant="outline" disabled={busy} onClick={() => void load()}>
              <RefreshCw className="mr-2 h-4 w-4" />{t('nodes.logs.reload')}
            </Button>
          </div>
          <CardDescription>{t('nodes.tokens.discoveryDescription')}</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          {canCreate && (
            <form className="space-y-3 rounded-lg border p-4" onSubmit={event => { event.preventDefault(); void createInvitation() }}>
              <div className="space-y-1.5">
                <Label htmlFor="discovery-label">{t('nodes.invitationLabel')}</Label>
                <Input id="discovery-label" value={label} maxLength={120} required
                  placeholder={t('nodes.invitationPlaceholder')}
                  onChange={event => setLabel(event.target.value)} />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="discovery-lifetime">{t('nodes.tokens.lifetime')}</Label>
                <select id="discovery-lifetime" className="border-input bg-background min-h-10 w-full rounded-md border px-3 text-sm"
                  value={lifetime} onChange={event => setLifetime(event.target.value)}>
                  <option value="0">{t('nodes.tokens.noExpiry')}</option>
                  <option value="24">{t('nodes.tokens.oneDay')}</option>
                  <option value="168">{t('nodes.tokens.sevenDays')}</option>
                  <option value="720">{t('nodes.tokens.thirtyDays')}</option>
                </select>
              </div>
              <label className="flex items-start gap-2 text-sm">
                <Checkbox checked={autoApprove} onCheckedChange={value => setAutoApprove(value === true)} />
                <span>{t('nodes.autoApproveNote')}</span>
              </label>
              <Button type="submit" disabled={busy || !label.trim()}>{t('nodes.createInvitation')}</Button>
            </form>
          )}
          {oneTime && (
            <div className="space-y-2 rounded-lg border border-primary/30 bg-primary/5 p-4" role="status">
              <p className="flex items-center gap-2 text-sm font-medium"><CheckCircle2 className="h-4 w-4" />{t('nodes.tokenReady')}</p>
              <Label htmlFor="discovery-once">DISCOVERY_TOKEN</Label>
              <div className="flex flex-wrap gap-2">
                <Input id="discovery-once" readOnly value={oneTime} autoComplete="off"
                  onFocus={e => e.target.select()} className="min-w-0 flex-1 font-mono text-xs" />
                <Button type="button" variant="outline" onClick={() => void copyOneTime()}>
                  <ClipboardCopy className="mr-2 h-4 w-4" />{t('nodes.copyToken')}
                </Button>
              </div>
              <p className="text-muted-foreground text-xs">{t('nodes.tokenOnceWarning')}</p>
              <Button size="sm" variant="ghost" type="button" onClick={() => setOneTime('')}>{t('nodes.tokens.hideToken')}</Button>
            </div>
          )}
          <div className="space-y-2">
            <h3 className="font-medium text-sm">{t('nodes.invitationList')} ({invitations.length})</h3>
            {invitations.length === 0 && <p className="text-muted-foreground text-sm">{t('nodes.tokens.emptyDiscovery')}</p>}
            {invitations.map(item => {
              const expired = Boolean(item.expires_at && new Date(item.expires_at).getTime() <= Date.now())
              const active = item.revoked !== '1' && !expired
              return <div key={item.id} className="space-y-3 rounded-lg border p-3">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <p className="min-w-0 break-words font-medium text-sm">{item.label}</p>
                  <Badge variant={active ? 'green' : 'outline'}>
                    {item.revoked === '1' ? t('nodes.invitationRevokedStatus') : expired ? t('nodes.tokens.expired') : t('nodes.tokens.active')}
                  </Badge>
                </div>
                <p className="text-muted-foreground text-xs">
                  {t('nodes.tokens.expiry')}: {item.expires_at ? new Date(item.expires_at).toLocaleString(i18n.language) : t('nodes.tokens.noExpiry')}
                  {' · '}{item.auto_approve === '1' ? t('nodes.invitationAutoStatus') : t('nodes.invitationManualStatus')}
                </p>
                {canUpdate && <div className="flex flex-wrap gap-2">
                  <Button size="sm" variant="outline" disabled={busy} onClick={() => void patchInvitation(item.id, { auto_approve: item.auto_approve !== '1' })}>
                    {item.auto_approve === '1' ? t('nodes.tokens.disableAuto') : t('nodes.tokens.enableAuto')}
                  </Button>
                  <Button size="sm" variant="outline" disabled={busy} onClick={() => void patchInvitation(item.id, { enabled: item.revoked === '1' })}>
                    {item.revoked === '1' ? t('nodes.tokens.enable') : t('nodes.tokens.disable')}
                  </Button>
                  <Button size="sm" variant="outline" disabled={busy}
                    onClick={() => void patchInvitation(item.id, { lifetime_hours: lifetimeHours() ?? 0 })}>
                    {t('nodes.tokens.extendExpiry')}
                  </Button>
                  <Button size="sm" variant="outline" disabled={busy} onClick={() => void rotateInvitation(item)}>{t('nodes.tokens.rotate')}</Button>
                  <Button size="sm" variant="ghost" disabled={busy} onClick={() => { setEditId(item.id); setEditLabel(item.label) }}>{t('nodes.tokens.rename')}</Button>
                </div>}
                {canUpdate && editId === item.id && <form className="flex flex-wrap gap-2" onSubmit={event => {
                  event.preventDefault(); if (editLabel.trim()) void patchInvitation(item.id, { label: editLabel.trim() })
                }}>
                  <Input aria-label={t('nodes.tokens.rename')} className="min-w-0 flex-1" maxLength={120} value={editLabel}
                    onChange={event => setEditLabel(event.target.value)} />
                  <Button size="sm" type="submit" disabled={busy || !editLabel.trim()}>{t('nodes.tokens.save')}</Button>
                </form>}
              </div>
            })}
          </div>
        </CardContent>
      </Card>

      <Card className="min-w-0 shadow-sm">
        <CardHeader className="gap-2">
          <div className="flex items-center gap-2"><Github className="h-5 w-5" /><CardTitle>{t('nodes.tokens.ghcrTitle')}</CardTitle></div>
          <CardDescription>{t('nodes.tokens.ghcrDescription')}</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          {ghcr && <div className="flex flex-wrap items-center gap-2 rounded-lg bg-muted/30 p-3 text-sm">
            {ghcr.configured ? <ShieldCheck className="h-4 w-4" /> : <ShieldOff className="h-4 w-4" />}
            <Badge variant={ghcr.configured ? 'green' : 'outline'}>
              {ghcr.configured ? t('nodes.tokens.ghcrConfigured') : t('nodes.tokens.ghcrMissing')}
            </Badge>
            <span className="min-w-0 break-all text-muted-foreground">{ghcr.repository}</span>
          </div>}
          <p className="text-muted-foreground text-sm">{t('nodes.tokens.ghcrAppliesToNodes')}</p>
          {canUpdate && <form className="space-y-3" onSubmit={event => { event.preventDefault(); void saveGHCR() }}>
            <div className="space-y-1.5">
              <Label htmlFor="ghcr-username">{t('nodes.tokens.ghcrUsername')}</Label>
              <Input id="ghcr-username" autoComplete="username" maxLength={128} value={login}
                placeholder="GitHub username" onChange={e => setLogin(e.target.value)} />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="ghcr-pat">{t('nodes.tokens.ghcrToken')}</Label>
              <Input id="ghcr-pat" type="password" autoComplete="new-password" maxLength={1024}
                value={pat} onChange={e => setPat(e.target.value)} placeholder={t('nodes.tokens.ghcrTokenPlaceholder')} />
              <p className="text-muted-foreground text-xs">{t('nodes.tokens.ghcrNeverReadable')}</p>
            </div>
            <div className="flex flex-wrap gap-2">
              <Button type="submit" disabled={busy || !pat || !login.trim()}>
                {ghcr?.configured ? t('nodes.tokens.replaceGHCR') : t('nodes.tokens.saveGHCR')}
              </Button>
              {ghcr?.configured && <Button type="button" variant="outline" disabled={busy} onClick={() => void deleteGHCR()}>
                {t('nodes.tokens.removeGHCR')}
              </Button>}
            </div>
          </form>}
          {ghcr?.updated_at && <p className="text-muted-foreground text-xs">
            {t('nodes.tokens.lastChanged')}: {new Date(ghcr.updated_at).toLocaleString(i18n.language)}
          </p>}
          <p className="text-muted-foreground text-xs">{t('nodes.tokens.readPackagesHint')}</p>
        </CardContent>
      </Card>
      {error && <p role="alert" className="text-destructive text-sm lg:col-span-2">{error}</p>}
    </div>
  )
}
