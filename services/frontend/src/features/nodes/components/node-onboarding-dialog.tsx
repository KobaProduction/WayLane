import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { RadioTower, Server, ShieldAlert, Copy, CheckCircle2 } from 'lucide-react'
import { toast } from 'sonner'
import { $fetch } from '@/service/http'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'

export type Invitation = {
  id: number
  label: string
  revoked: '0' | '1'
  created_at: string
  expires_at: string | null
  auto_approve: '0' | '1'
}

type OnboardingMode = 'classic' | 'discovery'

type NodeOnboardingDialogProps = {
  open: boolean
  onOpenChange: (open: boolean) => void
  onCreated: () => void
}

export default function NodeOnboardingDialog({ open, onOpenChange, onCreated }: NodeOnboardingDialogProps) {
  const { t } = useTranslation()
  const [mode, setMode] = useState<OnboardingMode>('discovery')
  const [label, setLabel] = useState('')
  const [autoApprove, setAutoApprove] = useState(false)
  const [invitations, setInvitations] = useState<Invitation[]>([])
  const [token, setToken] = useState('')
  const [direct, setDirect] = useState<{ node_uuid: string; node_token: string } | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  const loadInvitations = async () => {
    try {
      const response = await $fetch<{ invitations: Invitation[] }>('/api/node-registry/discovery-tokens')
      setInvitations(response.invitations)
      setError('')
    } catch {
      setError(t('nodes.invitationLoadError'))
    }
  }

  useEffect(() => {
    if (open) void loadInvitations()
    // Clear the one-time credential on route-driven close as well as explicit
    // dismissal. Only the currently open dialog may retain it in memory.
    else {
      setToken('')
      setDirect(null)
    }
  }, [open])

  const changeMode = (selected: OnboardingMode) => {
    if (busy) return
    if ((token || direct) && !window.confirm(t('nodes.discardTokenConfirm'))) return
    setMode(selected)
    setToken('')
    setDirect(null)
  }

  const changeOpen = (next: boolean) => {
    if (!next && (token || direct) && !window.confirm(t('nodes.discardTokenConfirm'))) return
    if (!next) {
      setToken('')
      setDirect(null)
      setLabel('')
      setAutoApprove(false)
      setMode('discovery')
    }
    onOpenChange(next)
  }

  const create = async () => {
    if (busy) return
    setBusy(true)
    setError('')
    try {
      if (mode === 'classic') {
        const reply = await $fetch<{ node_uuid: string; node_token: string }>(
          '/api/node-registry/classic', { method: 'POST' },
        )
        setDirect(reply)
        toast.success(t('nodes.classicCreated'))
      } else {
        if (!label.trim()) return
        const reply = await $fetch<{ discovery_token: string }>(
          '/api/node-registry/discovery-tokens', {
            method: 'POST', body: { label: label.trim(), auto_approve: autoApprove },
          },
        )
        setToken(reply.discovery_token)
        setLabel('')
        await loadInvitations()
        toast.success(t('nodes.invitationCreated'))
      }
      onCreated()
    } catch {
      setError(t('nodes.onboardingFailed'))
    } finally {
      setBusy(false)
    }
  }

  const revoke = async (invitation: Invitation) => {
    if (busy || !window.confirm(t('nodes.revokeInvitationConfirm', { name: invitation.label }))) return
    setBusy(true)
    try {
      await $fetch('/api/node-registry/discovery-tokens/' + invitation.id + '/revoke', { method: 'POST' })
      await loadInvitations()
      toast.success(t('nodes.invitationRevoked'))
    } catch {
      setError(t('nodes.invitationRevokeFailed'))
    } finally { setBusy(false) }
  }

  const copy = async (value: string) => {
    try {
      await navigator.clipboard.writeText(value)
      toast.success(t('nodes.tokenCopied'))
    } catch {
      toast.error(t('nodes.copyUnavailable'))
    }
  }

  const shownToken = mode === 'classic' ? direct?.node_token : token
  const tokenName = mode === 'classic' ? 'NODE_TOKEN' : 'DISCOVERY_TOKEN'

  return (
    <Dialog open={open} onOpenChange={changeOpen}>
      <DialogContent className="max-h-[90vh] w-[calc(100vw-1.5rem)] max-w-2xl overflow-y-auto p-5 sm:p-6">
        <DialogHeader>
          <DialogTitle>{t('nodes.onboardingTitle')}</DialogTitle>
          <DialogDescription>{t('nodes.onboardingDescription')}</DialogDescription>
        </DialogHeader>
        <div className="grid gap-3 sm:grid-cols-2" role="group" aria-label={t('nodes.registrationMode')}>
          <button type="button" disabled={busy} aria-pressed={mode === 'discovery'}
            className={`flex min-h-24 items-start gap-3 rounded-lg border p-4 text-left transition-colors focus-visible:outline-2 focus-visible:outline-primary ${mode === 'discovery' ? 'border-primary bg-primary/5' : 'hover:bg-muted/40'}`}
            onClick={() => changeMode('discovery')}>
            <RadioTower className="mt-0.5 h-5 w-5 shrink-0" aria-hidden />
            <span><strong className="block text-sm">Discovery</strong>
              <span className="text-muted-foreground mt-1 block text-xs">{t('nodes.discoveryModeDescription')}</span></span>
          </button>
          <button type="button" disabled={busy} aria-pressed={mode === 'classic'}
            className={`flex min-h-24 items-start gap-3 rounded-lg border p-4 text-left transition-colors focus-visible:outline-2 focus-visible:outline-primary ${mode === 'classic' ? 'border-primary bg-primary/5' : 'hover:bg-muted/40'}`}
            onClick={() => changeMode('classic')}>
            <Server className="mt-0.5 h-5 w-5 shrink-0" aria-hidden />
            <span><strong className="block text-sm">Classic</strong>
              <span className="text-muted-foreground mt-1 block text-xs">{t('nodes.classicModeDescription')}</span></span>
          </button>
        </div>
        {shownToken ? (
          <div className="space-y-3 rounded-lg border border-primary/30 bg-primary/5 p-4" role="status">
            <p className="flex items-center gap-2 text-sm font-medium"><CheckCircle2 className="h-4 w-4" />{t('nodes.tokenReady')}</p>
            {direct && <p className="break-all text-xs text-muted-foreground">UUID: {direct.node_uuid}</p>}
            <Label htmlFor="node-onboarding-token">{tokenName}</Label>
            <div className="flex flex-col gap-2 sm:flex-row">
              <Input id="node-onboarding-token" value={shownToken} readOnly autoComplete="off"
                onFocus={event => event.target.select()} className="min-w-0 flex-1 font-mono text-xs" />
              <Button type="button" variant="outline" onClick={() => void copy(`${tokenName}=${shownToken}`)}>
                <Copy className="mr-2 h-4 w-4" />{t('nodes.copyToken')}
              </Button>
            </div>
            <p className="text-muted-foreground text-xs">{t('nodes.tokenOnceWarning')}</p>
          </div>
        ) : mode === 'discovery' ? (
          <form className="space-y-3" onSubmit={event => { event.preventDefault(); void create() }}>
            <div className="space-y-2">
              <Label htmlFor="node-discovery-label">{t('nodes.invitationLabel')}</Label>
              <Input id="node-discovery-label" required maxLength={120} value={label}
                placeholder={t('nodes.invitationPlaceholder')}
                onChange={event => setLabel(event.target.value)} />
            </div>
            <label className="flex items-start gap-2 text-sm">
              <Checkbox checked={autoApprove} onCheckedChange={value => setAutoApprove(value === true)} />
              <span>{t('nodes.autoApproveNote')}</span>
            </label>
            <Button type="submit" disabled={!label.trim() || busy}>{t('nodes.createInvitation')}</Button>
          </form>
        ) : (
          <div className="space-y-3">
            <p className="text-muted-foreground text-sm">{t('nodes.classicSetupExplanation')}</p>
            <Button type="button" disabled={busy} onClick={() => void create()}>{t('nodes.createClassic')}</Button>
          </div>
        )}
        {error && <p role="alert" className="text-destructive text-sm">{error}</p>}
        <div className="flex items-start gap-2 rounded-md bg-muted/40 p-3 text-xs text-muted-foreground">
          <ShieldAlert className="h-4 w-4 shrink-0" aria-hidden />
          <p>{t('nodes.onboardingDeployHint')}</p>
        </div>
        {mode === 'discovery' && invitations.length > 0 && (
          <section className="space-y-2 border-t pt-4" aria-label={t('nodes.invitationList')}>
            <h3 className="text-sm font-medium">{t('nodes.invitationList')} ({invitations.length})</h3>
            {invitations.map(invitation => (
              <div key={invitation.id} className="flex flex-wrap items-center justify-between gap-2 rounded-md border p-2 text-xs">
                <div className="min-w-0">
                  <p className="break-all font-medium">{invitation.label}</p>
                  <p className="text-muted-foreground">{invitation.revoked === '1' ? t('nodes.invitationRevokedStatus') :
                    invitation.auto_approve === '1' ? t('nodes.invitationAutoStatus') : t('nodes.invitationManualStatus')}</p>
                </div>
                {invitation.revoked !== '1' && (
                  <Button size="sm" variant="outline" type="button" disabled={busy}
                    onClick={() => void revoke(invitation)}>{t('nodes.revokeInvitation')}</Button>
                )}
              </div>
            ))}
          </section>
        )}
      </DialogContent>
    </Dialog>
  )
}
