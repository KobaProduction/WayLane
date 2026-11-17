import { useCallback, useEffect, useRef, useState } from 'react'
import { useSearchParams } from 'react-router'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'
import { Switch } from '@/components/ui/switch'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import {
  AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent,
  AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle,
} from '@/components/ui/alert-dialog'
import { $fetch } from '@/service/http'
import {
  FIREWALL_SCENARIOS, FIREWALL_SCENARIO_KEYS,
  firewallDraftChanged, validFirewallScenario, validFirewallTab,
  validateFirewallDraft, validateMessageTemplate, validateWebhookFields,
} from '../firewall-workspace-contract'
import type { FirewallEditor, FirewallScenario, PreviewResponse } from '../firewall-workspace-contract'
import { SubscriptionFirewallEvents } from './subscription-firewall-events'

const base = '/api/subscription-firewall'

function errorMessage(error: unknown, translate: (key: string) => string): string {
  const err = error as { status?: number; data?: { detail?: unknown }; response?: { data?: { detail?: unknown } } }
  const detail = err?.data?.detail ?? err?.response?.data?.detail
  if (err?.status === 403) return translate('permissionDenied')
  if (err?.status === 422) {
    return typeof detail === 'string' && detail.includes('WAYLANE_DEVICE_WEBHOOK_SECRET')
      ? translate('webhookSecretMissing') : translate('serverRejected')
  }
  if ((err?.status ?? 0) >= 500) return translate('backendUnavailable')
  return translate('unknownError')
}

/** Read-only preview: this isn't a live subscription request or an admission-policy decision. */
function MessagePreview({ preview, scenario, currentSignature }: {
  preview: { scenario: FirewallScenario; signature: string; data: PreviewResponse } | null
  scenario: FirewallScenario
  currentSignature: string
}) {
  const { t } = useTranslation()
  const outdated = Boolean(preview && (preview.scenario !== scenario || preview.signature !== currentSignature))
  if (!preview) return <p className="rounded-lg border border-dashed p-6 text-sm text-muted-foreground">{t('subscriptionFirewall.ui.previewNotReady')}</p>
  if (outdated) return <p role="status" className="rounded-lg border border-dashed p-6 text-sm text-muted-foreground">{t('subscriptionFirewall.ui.previewStale')}</p>
  return <div className="space-y-3 rounded-lg border p-4" data-testid="firewall-preview-result">
    <div className="space-y-1">
      <p className="font-semibold">{preview.data.title}</p>
      <p className="text-xs text-muted-foreground">{t('subscriptionFirewall.ui.previewExplanation')}</p>
    </div>
    <div className="space-y-1 rounded-md bg-muted/40 px-3 py-3">
      {preview.data.lines.map((line, index) => <p key={index} className="break-words text-sm">{line}</p>)}
    </div>
    <details className="text-xs text-muted-foreground">
      <summary className="cursor-pointer font-medium">{t('subscriptionFirewall.ui.technicalFields')}</summary>
      <p className="mt-2 font-medium">{t('subscriptionFirewall.ui.announceHeader')}</p>
      <code className="block break-all">{preview.data.announce}</code>
      <p className="mt-2 font-medium">{t('subscriptionFirewall.ui.responseBody')}</p>
      <code className="block break-all">{preview.data.base64_payload}</code>
    </details>
  </div>
}

export function SubscriptionFirewallDesigner({ canUpdate = true }: { canUpdate?: boolean }) {
  const { t, i18n } = useTranslation()
  // Keep async loading independent from language changes; the i18n object is stable.
  const localizeError = (key: string) => i18n.t(`subscriptionFirewall.ui.${key}`)
  const [params, setParams] = useSearchParams()
  const tab = validFirewallTab(params.get('tab'))
  const scenario = validFirewallScenario(params.get('scenario'))
  const [config, setConfig] = useState<FirewallEditor | null>(null)
  const [savedConfig, setSavedConfig] = useState<FirewallEditor | null>(null)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [previewBusy, setPreviewBusy] = useState(false)
  const [loadError, setLoadError] = useState('')
  const [saveError, setSaveError] = useState('')
  const [etag, setEtag] = useState<string | null>(null)
  const [writeConflict, setWriteConflict] = useState(false)
  const [reloadPromptOpen, setReloadPromptOpen] = useState(false)
  const [preview, setPreview] = useState<{ scenario: FirewallScenario; signature: string; data: PreviewResponse } | null>(null)
  const revision = useRef(0)

  const loadConfig = useCallback(async () => {
    const request = ++revision.current
    setLoading(true)
    setLoadError('')
    try {
      const response = await $fetch.raw<FirewallEditor>(base)
      if (revision.current !== request) return
      if (!response._data) throw new Error('Backend returned no firewall configuration')
      setConfig(response._data)
      setSavedConfig(response._data)
      setEtag(response.headers.get('etag'))
      setWriteConflict(false)
      setPreview(null)
      setSaveError('')
    } catch (error) {
      if (revision.current === request) setLoadError(errorMessage(error, localizeError))
    } finally {
      if (revision.current === request) setLoading(false)
    }
  }, [i18n])

  useEffect(() => {
    void loadConfig()
    return () => { revision.current += 1 }
  }, [loadConfig])

  const dirty = firewallDraftChanged(savedConfig, config)
  const translateValidation = (key: string, defaultValue: string) => t(`subscriptionFirewall.ui.${key}`, { defaultValue })
  const problems = config ? validateFirewallDraft(config, translateValidation) : []
  const template = config?.scenarios[scenario]
  const templateErrors = template ? validateMessageTemplate(template, translateValidation) : []
  const previewSignature = template ? JSON.stringify(template) : ''

  function changeTab(value: string) {
    const next = validFirewallTab(value)
    const query = new URLSearchParams(params)
    if (next === 'messages') query.delete('tab')
    else query.set('tab', next)
    setParams(query)
  }

  function changeScenario(key: FirewallScenario) {
    const query = new URLSearchParams(params)
    if (key === 'invalid_link') query.delete('scenario')
    else query.set('scenario', key)
    setParams(query)
  }

  function updateTemplate(field: 'title' | 'lines', value: string) {
    if (!canUpdate) return
    setConfig(current => {
      if (!current) return current
      const existing = current.scenarios[scenario]
      return { ...current, scenarios: {
        ...current.scenarios,
        [scenario]: { ...existing, [field]: field === 'lines' ? value.split('\n') : value },
      } }
    })
    setSaveError('')
  }

  function updateSetting(patch: Partial<FirewallEditor>) {
    if (!canUpdate) return
    setConfig(current => current ? { ...current, ...patch } : current)
    setSaveError('')
  }

  async function save() {
    if (!canUpdate || !config || !dirty || problems.length > 0 || saving || !etag || writeConflict) return
    const payload = config
    setSaving(true)
    setSaveError('')
    try {
      const response = await $fetch.raw<FirewallEditor>(base, { method: 'PUT', body: payload, headers: { 'If-Match': etag } })
      if (!response._data || !response.headers.get('etag')) throw new Error('Backend did not confirm the saved configuration version')
      setConfig(response._data)
      setSavedConfig(response._data)
      setEtag(response.headers.get('etag'))
      setWriteConflict(false)
      toast.success(t('subscriptionFirewall.ui.settingsSaved'))
    } catch (error) {
      if ((error as { status?: number })?.status === 409) {
        setWriteConflict(true)
        setSaveError(t('subscriptionFirewall.ui.conflictDesc'))
      } else {
        setSaveError(errorMessage(error, localizeError))
        toast.error(t('subscriptionFirewall.ui.writeFailedToast'))
      }
    } finally { setSaving(false) }
  }

  async function formPreview() {
    if (!template || templateErrors.length > 0 || previewBusy) return
    setPreviewBusy(true)
    const signature = JSON.stringify(template)
    try {
      const result = await $fetch<PreviewResponse>(base + '/preview/' + scenario, { method: 'POST', body: template })
      setPreview({ scenario, signature, data: result })
    } catch (error) {
      toast.error(errorMessage(error, localizeError))
    } finally { setPreviewBusy(false) }
  }

  return <div className="min-w-0 space-y-4 px-4 py-4" data-testid="firewall-workspace">
    {!canUpdate && <p role="status" className="rounded-lg border p-3 text-sm text-muted-foreground">{t('subscriptionFirewall.ui.readOnly')}</p>}
    <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border px-4 py-3">
      <div className="space-y-1">
        <p className="text-sm font-medium">{t('subscriptionFirewall.ui.configTitle')}</p>
        <p className="text-xs text-muted-foreground" role="status">
          {loading && !savedConfig ? t('subscriptionFirewall.ui.loadingSettings') : !config ? t('subscriptionFirewall.ui.settingsUnavailable') : dirty ? t('subscriptionFirewall.ui.unsaved') : t('subscriptionFirewall.ui.clean')}
        </p>
      </div>
      <div className="flex flex-wrap gap-2">
        <Button type="button" size="sm" variant="outline" disabled={loading || saving} onClick={() => dirty ? setReloadPromptOpen(true) : void loadConfig()}>
          {t('subscriptionFirewall.ui.reload')}
        </Button>
        <Button type="button" size="sm" disabled={!canUpdate || !dirty || problems.length > 0 || saving || loading || !etag || writeConflict} onClick={() => void save()}>
          {saving ? t('subscriptionFirewall.ui.saving') : t('subscriptionFirewall.ui.saveChanges')}
        </Button>
      </div>
    </div>
    {loadError && <div role="alert" className="space-y-2 rounded-lg border border-destructive/40 p-3 text-sm text-destructive">
      <p>{t('subscriptionFirewall.ui.loadFailed')}: {loadError}</p>
      <Button type="button" size="sm" variant="outline" disabled={loading} onClick={() => dirty ? setReloadPromptOpen(true) : void loadConfig()}>{t('subscriptionFirewall.ui.retryLoad')}</Button>
    </div>}
    {config && !etag && <p role="alert" className="rounded-md border border-destructive/40 p-3 text-sm text-destructive">
      {t('subscriptionFirewall.ui.noVersion')}
    </p>}
    {writeConflict && <div role="alert" className="space-y-2 rounded-md border border-destructive/40 p-3 text-sm">
      <p className="font-medium">{t('subscriptionFirewall.ui.conflictTitle')}</p>
      <p>{t('subscriptionFirewall.ui.conflictDesc')}</p>
      <Button type="button" variant="outline" size="sm" onClick={() => setReloadPromptOpen(true)}>{t('subscriptionFirewall.ui.loadServer')}</Button>
    </div>}
    {saveError && !writeConflict && <p role="alert" className="break-words rounded-md border border-destructive/40 p-3 text-sm text-destructive">{t('subscriptionFirewall.ui.saveFailed')}: {saveError}</p>}
    {problems.length > 0 && <div role="alert" className="rounded-lg border border-destructive/40 p-3 text-sm">
      <p className="font-medium">{t('subscriptionFirewall.ui.fixBeforeSave')}</p>
      <ul className="mt-1 list-disc space-y-1 pl-5 text-destructive">{problems.map((problem, index) => <li key={`${index}:${problem}`}>{problem}</li>)}</ul>
    </div>}
    <Tabs value={tab} onValueChange={changeTab} className="w-full space-y-4">
      <TabsList className="flex h-auto w-full flex-wrap justify-start gap-1" aria-label={t('subscriptionFirewall.ui.sections')}>
        <TabsTrigger className="min-h-10 flex-1 sm:flex-none" value="messages">{t('subscriptionFirewall.ui.scenariosTab')}</TabsTrigger>
        <TabsTrigger className="min-h-10 flex-1 sm:flex-none" value="preview">{t('subscriptionFirewall.ui.previewTab')}</TabsTrigger>
        <TabsTrigger className="min-h-10 flex-1 sm:flex-none" value="webhook">Webhook</TabsTrigger>
        <TabsTrigger className="min-h-10 flex-1 sm:flex-none" value="events">{t('subscriptionFirewall.ui.eventsTab')}</TabsTrigger>
      </TabsList>
      <TabsContent value="messages" className="space-y-4">
        {config && <>
          <div className="flex flex-wrap items-start justify-between gap-3 rounded-lg border px-4 py-3">
            <div className="max-w-xl space-y-1">
              <p className="text-sm font-medium">{t('subscriptionFirewall.ui.responsesTitle')}</p>
              <p className="text-xs text-muted-foreground">{t('subscriptionFirewall.ui.responsesDescription')}</p>
            </div>
            <label className="flex items-center gap-2 text-sm">
              <Switch checked={config.enabled} disabled={!canUpdate || saving} onCheckedChange={enabled => updateSetting({ enabled })} />
              {config.enabled ? t('subscriptionFirewall.ui.enabled') : t('subscriptionFirewall.ui.disabled')}
            </label>
          </div>
          <div className="grid min-w-0 gap-4 lg:grid-cols-[minmax(12rem,16rem)_minmax(0,1fr)]">
            <nav aria-label={t('subscriptionFirewall.ui.scenarioNav')} className="min-w-0 space-y-1 rounded-lg border p-2">
              {FIREWALL_SCENARIO_KEYS.map(key => <button type="button" key={key}
                aria-current={key === scenario ? 'page' : undefined}
                onClick={() => changeScenario(key)}
                className={`w-full rounded-md px-3 py-3 text-left text-sm transition-colors focus-visible:ring-2 focus-visible:ring-ring ${key === scenario ? 'bg-primary/10 font-medium text-foreground' : 'text-muted-foreground hover:bg-muted hover:text-foreground'}`}>
                {t(`subscriptionFirewall.ui.scenario.${key}`, { defaultValue: FIREWALL_SCENARIOS[key] })}
              </button>)}
            </nav>
            <section className="min-w-0 space-y-4 rounded-lg border p-4" aria-label={t('subscriptionFirewall.ui.scenarioEditor')}>
              <div className="space-y-1">
                <h3 className="text-base font-semibold">{t(`subscriptionFirewall.ui.scenario.${scenario}`, { defaultValue: FIREWALL_SCENARIOS[scenario] })}</h3>
                <p className="text-xs text-muted-foreground">{t('subscriptionFirewall.ui.scenarioDescription')}</p>
              </div>
              <label className="block space-y-2 text-sm">
                <span>{t('subscriptionFirewall.ui.subject')}</span>
                <Input disabled={!canUpdate || saving} aria-label={t('subscriptionFirewall.ui.titleInput')} value={template?.title ?? ''} maxLength={256}
                  onChange={event => updateTemplate('title', event.target.value)} />
              </label>
              <label className="block space-y-2 text-sm">
                <span>{t('subscriptionFirewall.ui.lines')} <span className="text-muted-foreground">{t('subscriptionFirewall.ui.linesHint')}</span></span>
                <Textarea disabled={!canUpdate || saving} aria-label={t('subscriptionFirewall.ui.linesInput')} rows={5} value={template?.lines.join('\n') ?? ''}
                  onChange={event => updateTemplate('lines', event.target.value)} />
              </label>
              {templateErrors.length > 0 && <p role="alert" className="text-sm text-destructive">{templateErrors.join(' ')}</p>}
              <div className="flex flex-wrap items-center justify-between gap-2">
                <p className="text-xs text-muted-foreground">{t('subscriptionFirewall.ui.singleSaveHint')}</p>
                <Button type="button" variant="outline" disabled={templateErrors.length > 0} onClick={() => changeTab('preview')}>{t('subscriptionFirewall.ui.openPreview')}</Button>
              </div>
            </section>
          </div>
        </>}
        {!config && !loadError && <p className="text-sm text-muted-foreground">{t('subscriptionFirewall.ui.loadingScenarios')}</p>}
      </TabsContent>
      <TabsContent value="preview" className="space-y-4">
        <section className="min-w-0 space-y-4 rounded-lg border p-4">
          <div className="space-y-1">
            <h3 className="text-base font-semibold">{t('subscriptionFirewall.ui.previewHeading')}</h3>
            <p className="text-sm text-muted-foreground">{t('subscriptionFirewall.ui.previewDescription')}</p>
          </div>
          {config && <>
            <label className="block space-y-2 text-sm">
              <span>{t('subscriptionFirewall.ui.scenarioLabel')}</span>
              <select className="bg-background h-10 w-full rounded-md border px-3" value={scenario}
                aria-label={t('subscriptionFirewall.ui.previewScenario')} onChange={event => changeScenario(validFirewallScenario(event.target.value))}>
                {FIREWALL_SCENARIO_KEYS.map(key => <option value={key} key={key}>{t(`subscriptionFirewall.ui.scenario.${key}`, { defaultValue: FIREWALL_SCENARIOS[key] })}</option>)}
              </select>
            </label>
            {templateErrors.length > 0 && <p role="alert" className="text-sm text-destructive">{templateErrors.join(' ')}</p>}
            <Button type="button" disabled={previewBusy || templateErrors.length > 0} onClick={() => void formPreview()}>
              {previewBusy ? t('subscriptionFirewall.ui.generating') : t('subscriptionFirewall.ui.generate')}
            </Button>
            <MessagePreview preview={preview} scenario={scenario} currentSignature={previewSignature} />
            <details className="text-xs text-muted-foreground">
              <summary className="cursor-pointer">{t('subscriptionFirewall.ui.whenUsed')}</summary>
              <p className="mt-2">{t('subscriptionFirewall.ui.whenUsedDescription')}</p>
            </details>
          </>}
        </section>
      </TabsContent>
      <TabsContent value="webhook" className="space-y-4">
        <section className="min-w-0 space-y-4 rounded-lg border p-4">
          <div className="space-y-1">
            <h3 className="text-base font-semibold">{t('subscriptionFirewall.ui.webhookHeading')}</h3>
            <p className="text-sm text-muted-foreground">{t('subscriptionFirewall.ui.webhookDescription')}</p>
          </div>
          {config && <>
            <label className="flex items-center gap-3 text-sm">
              <Switch disabled={!canUpdate || saving} checked={config.webhook_enabled} onCheckedChange={webhook_enabled => updateSetting({ webhook_enabled })} />
              {t('subscriptionFirewall.ui.sendDeviceEvents')}
            </label>
            <label className="block space-y-2 text-sm">
              <span>{t('subscriptionFirewall.ui.webhookURL')}</span>
              <Input disabled={!canUpdate || saving} aria-label={t('subscriptionFirewall.ui.webhookURL')} type="url" autoComplete="off" placeholder="https://example.com/device-events"
                value={config.webhook_url ?? ''} maxLength={2048}
                onChange={event => updateSetting({ webhook_url: event.target.value || null })} />
            </label>
            <details className="space-y-3 text-sm">
              <summary className="cursor-pointer font-medium">{t('subscriptionFirewall.ui.legacyCheck')}</summary>
              <label className="mt-3 block space-y-2 text-sm">
                <span>URL `/webhook/check_device`</span>
                <Input disabled={!canUpdate || saving} aria-label={t('subscriptionFirewall.ui.legacyURL')} type="url" autoComplete="off" placeholder="https://example.com/webhook/check_device"
                  value={config.device_check_url ?? ''} maxLength={2048}
                  onChange={event => updateSetting({ device_check_url: event.target.value || null })} />
              </label>
            </details>
            {validateWebhookFields(config, translateValidation).length > 0 && <p role="alert" className="text-sm text-destructive">{validateWebhookFields(config, translateValidation).join(' ')}</p>}
            <p className="text-xs text-muted-foreground">{t('subscriptionFirewall.ui.webhookSecurity')}</p>
            <p className="text-xs text-muted-foreground">{t('subscriptionFirewall.ui.webhookSaveNote')}</p>
          </>}
        </section>
      </TabsContent>
      <TabsContent value="events" className="space-y-4">
        <SubscriptionFirewallEvents webhookEnabled={Boolean(savedConfig?.webhook_enabled)} canUpdate={canUpdate} />
      </TabsContent>
    </Tabs>
    <AlertDialog open={reloadPromptOpen} onOpenChange={setReloadPromptOpen}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>{t('subscriptionFirewall.ui.discardTitle')}</AlertDialogTitle>
          <AlertDialogDescription>{t('subscriptionFirewall.ui.discardDescription')}</AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel>{t('subscriptionFirewall.ui.keepEditing')}</AlertDialogCancel>
          <AlertDialogAction onClick={() => void loadConfig()}>{t('subscriptionFirewall.ui.discardLoad')}</AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  </div>
}
