import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useNavigate } from 'react-router'
import { RadioTower, Server, ShieldAlert, Copy, CheckCircle2 } from 'lucide-react'
import { toast } from 'sonner'
import { $fetch } from '@/service/http'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'

type OnboardingMode = 'classic' | 'discovery'

type NodeOnboardingDialogProps = {
  open: boolean
  onOpenChange: (open: boolean) => void
  onCreated: () => void
}

export default function NodeOnboardingDialog({ open, onOpenChange, onCreated }: NodeOnboardingDialogProps) {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const [mode, setMode] = useState<OnboardingMode>('discovery')
  const [direct, setDirect] = useState<{ node_uuid: string; node_token: string } | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    if (!open) setDirect(null)
  }, [open])

  const changeOpen = (next: boolean) => {
    if (!next && direct && !window.confirm(t('nodes.discardTokenConfirm'))) return
    if (!next) {
      setDirect(null)
      setMode('discovery')
      setError('')
    }
    onOpenChange(next)
  }

  const createClassic = async () => {
    if (busy) return
    setBusy(true)
    setError('')
    try {
      const reply = await $fetch<{ node_uuid: string; node_token: string }>(
        '/api/node-registry/classic', { method: 'POST' },
      )
      setDirect(reply)
      toast.success(t('nodes.classicCreated'))
      onCreated()
    } catch { setError(t('nodes.onboardingFailed')) }
    finally { setBusy(false) }
  }

  const goToTokens = () => {
    onOpenChange(false)
    navigate('/nodes/tokens')
  }

  const copy = async () => {
    if (!direct) return
    try {
      await navigator.clipboard.writeText('NODE_TOKEN=' + direct.node_token)
      toast.success(t('nodes.tokenCopied'))
    } catch { toast.error(t('nodes.copyUnavailable')) }
  }

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
            onClick={() => { if (direct && !window.confirm(t('nodes.discardTokenConfirm'))) return; setMode('discovery'); setDirect(null) }}>
            <RadioTower className="mt-0.5 h-5 w-5 shrink-0" aria-hidden />
            <span><strong className="block text-sm">Discovery</strong>
              <span className="text-muted-foreground mt-1 block text-xs">{t('nodes.discoveryModeDescription')}</span></span>
          </button>
          <button type="button" disabled={busy} aria-pressed={mode === 'classic'}
            className={`flex min-h-24 items-start gap-3 rounded-lg border p-4 text-left transition-colors focus-visible:outline-2 focus-visible:outline-primary ${mode === 'classic' ? 'border-primary bg-primary/5' : 'hover:bg-muted/40'}`}
            onClick={() => setMode('classic')}>
            <Server className="mt-0.5 h-5 w-5 shrink-0" aria-hidden />
            <span><strong className="block text-sm">Classic</strong>
              <span className="text-muted-foreground mt-1 block text-xs">{t('nodes.classicModeDescription')}</span></span>
          </button>
        </div>
        {direct && mode === 'classic' ? (
          <div className="space-y-3 rounded-lg border border-primary/30 bg-primary/5 p-4" role="status">
            <p className="flex items-center gap-2 text-sm font-medium"><CheckCircle2 className="h-4 w-4" />{t('nodes.tokenReady')}</p>
            <p className="break-all text-xs text-muted-foreground">UUID: {direct.node_uuid}</p>
            <Label htmlFor="node-onboarding-token">NODE_TOKEN</Label>
            <div className="flex flex-col gap-2 sm:flex-row">
              <Input id="node-onboarding-token" value={direct.node_token} readOnly autoComplete="off"
                onFocus={event => event.target.select()} className="min-w-0 flex-1 font-mono text-xs" />
              <Button type="button" variant="outline" onClick={() => void copy()}>
                <Copy className="mr-2 h-4 w-4" />{t('nodes.copyToken')}
              </Button>
            </div>
            <p className="text-muted-foreground text-xs">{t('nodes.tokenOnceWarning')}</p>
          </div>
        ) : mode === 'discovery' ? (
          <div className="space-y-3 rounded-md border p-4">
            <p className="text-muted-foreground text-sm">{t('nodes.tokens.discoverySetupHint')}</p>
            <Button type="button" onClick={goToTokens}>{t('nodes.tokens.openTab')}</Button>
          </div>
        ) : (
          <div className="space-y-3">
            <p className="text-muted-foreground text-sm">{t('nodes.classicSetupExplanation')}</p>
            <Button type="button" disabled={busy} onClick={() => void createClassic()}>{t('nodes.createClassic')}</Button>
          </div>
        )}
        {error && <p role="alert" className="text-destructive text-sm">{error}</p>}
        <div className="flex items-start gap-2 rounded-md bg-muted/40 p-3 text-xs text-muted-foreground">
          <ShieldAlert className="h-4 w-4 shrink-0" aria-hidden />
          <p>{t('nodes.onboardingDeployHint')}</p>
        </div>
      </DialogContent>
    </Dialog>
  )
}
