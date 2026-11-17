import { useCallback, useEffect, useRef, useState } from 'react'
import { useSearchParams } from 'react-router'
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

function errorMessage(error: unknown): string {
  const err = error as { data?: { detail?: unknown }; response?: { data?: { detail?: unknown } }; message?: string }
  const detail = err?.data?.detail ?? err?.response?.data?.detail
  if (typeof detail === 'string') return detail
  return error instanceof Error ? error.message : 'Не удалось выполнить запрос'
}

/** Read-only preview: this isn't a live subscription request or an admission-policy decision. */
function MessagePreview({ preview, scenario, currentSignature }: {
  preview: { scenario: FirewallScenario; signature: string; data: PreviewResponse } | null
  scenario: FirewallScenario
  currentSignature: string
}) {
  const outdated = Boolean(preview && (preview.scenario !== scenario || preview.signature !== currentSignature))
  if (!preview) return <p className="rounded-lg border border-dashed p-6 text-sm text-muted-foreground">Предпросмотр ещё не сформирован. Выберите сценарий и нажмите «Сформировать».</p>
  if (outdated) return <p role="status" className="rounded-lg border border-dashed p-6 text-sm text-muted-foreground">Шаблон изменился. Сформируйте предпросмотр заново, чтобы не видеть устаревший результат.</p>
  return <div className="space-y-3 rounded-lg border p-4" data-testid="firewall-preview-result">
    <div className="space-y-1">
      <p className="font-semibold">{preview.data.title}</p>
      <p className="text-xs text-muted-foreground">Результат Backend для выбранного шаблона · Base64 + announce. Не является проверкой подписки пользователя.</p>
    </div>
    <div className="space-y-1 rounded-md bg-muted/40 px-3 py-3">
      {preview.data.lines.map((line, index) => <p key={index} className="break-words text-sm">{line}</p>)}
    </div>
    <details className="text-xs text-muted-foreground">
      <summary className="cursor-pointer font-medium">Технические поля ответа</summary>
      <p className="mt-2 font-medium">Заголовок announce</p>
      <code className="block break-all">{preview.data.announce}</code>
      <p className="mt-2 font-medium">Тело ответа (Base64)</p>
      <code className="block break-all">{preview.data.base64_payload}</code>
    </details>
  </div>
}

export function SubscriptionFirewallDesigner({ canUpdate = true }: { canUpdate?: boolean }) {
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
  const [reloadPromptOpen, setReloadPromptOpen] = useState(false)
  const [preview, setPreview] = useState<{ scenario: FirewallScenario; signature: string; data: PreviewResponse } | null>(null)
  const revision = useRef(0)

  const loadConfig = useCallback(async () => {
    const request = ++revision.current
    setLoading(true)
    setLoadError('')
    try {
      const saved = await $fetch<FirewallEditor>(base)
      if (revision.current !== request) return
      setConfig(saved)
      setSavedConfig(saved)
      setPreview(null)
      setSaveError('')
    } catch (error) {
      if (revision.current === request) setLoadError(errorMessage(error))
    } finally {
      if (revision.current === request) setLoading(false)
    }
  }, [])

  useEffect(() => {
    void loadConfig()
    return () => { revision.current += 1 }
  }, [loadConfig])

  const dirty = firewallDraftChanged(savedConfig, config)
  const problems = config ? validateFirewallDraft(config) : []
  const template = config?.scenarios[scenario]
  const templateErrors = template ? validateMessageTemplate(template) : []
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
    if (!canUpdate || !config || !dirty || problems.length > 0 || saving) return
    const payload = config
    setSaving(true)
    setSaveError('')
    try {
      const response = await $fetch<FirewallEditor>(base, { method: 'PUT', body: payload })
      setConfig(response)
      setSavedConfig(response)
      toast.success('Настройки Subscription Firewall сохранены в Backend')
    } catch (error) {
      setSaveError(errorMessage(error))
      toast.error('Настройки не сохранены. Исправьте ошибку и повторите.')
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
      toast.error(errorMessage(error))
    } finally { setPreviewBusy(false) }
  }

  return <div className="min-w-0 space-y-4 px-4 py-4" data-testid="firewall-workspace">
    {!canUpdate && <p role="status" className="rounded-lg border p-3 text-sm text-muted-foreground">Режим просмотра: изменение конфигурации и ручная доставка событий недоступны для этой роли.</p>}
    <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border px-4 py-3">
      <div className="space-y-1">
        <p className="text-sm font-medium">Конфигурация Firewall</p>
        <p className="text-xs text-muted-foreground" role="status">
          {loading && !savedConfig ? 'Загрузка настроек…' : !config ? 'Настройки недоступны' : dirty ? 'Есть несохранённые изменения' : 'Нет несохранённых изменений'}
        </p>
      </div>
      <div className="flex flex-wrap gap-2">
        <Button type="button" size="sm" variant="outline" disabled={loading || saving} onClick={() => dirty ? setReloadPromptOpen(true) : void loadConfig()}>
          Перезагрузить
        </Button>
        <Button type="button" size="sm" disabled={!canUpdate || !dirty || problems.length > 0 || saving || loading} onClick={() => void save()}>
          {saving ? 'Сохранение…' : 'Сохранить изменения'}
        </Button>
      </div>
    </div>
    {loadError && <div role="alert" className="space-y-2 rounded-lg border border-destructive/40 p-3 text-sm text-destructive">
      <p>Не удалось загрузить конфигурацию: {loadError}</p>
      <Button type="button" size="sm" variant="outline" disabled={loading} onClick={() => dirty ? setReloadPromptOpen(true) : void loadConfig()}>Повторить загрузку</Button>
    </div>}
    {saveError && <p role="alert" className="break-words rounded-md border border-destructive/40 p-3 text-sm text-destructive">Не удалось сохранить: {saveError}</p>}
    {problems.length > 0 && <div role="alert" className="rounded-lg border border-destructive/40 p-3 text-sm">
      <p className="font-medium">Перед сохранением исправьте ошибки:</p>
      <ul className="mt-1 list-disc space-y-1 pl-5 text-destructive">{problems.map((problem, index) => <li key={`${index}:${problem}`}>{problem}</li>)}</ul>
    </div>}
    <Tabs value={tab} onValueChange={changeTab} className="w-full space-y-4">
      <TabsList className="flex h-auto w-full flex-wrap justify-start gap-1" aria-label="Разделы Subscription Firewall">
        <TabsTrigger className="min-h-10 flex-1 sm:flex-none" value="messages">Сценарии</TabsTrigger>
        <TabsTrigger className="min-h-10 flex-1 sm:flex-none" value="preview">Предпросмотр</TabsTrigger>
        <TabsTrigger className="min-h-10 flex-1 sm:flex-none" value="webhook">Webhook</TabsTrigger>
        <TabsTrigger className="min-h-10 flex-1 sm:flex-none" value="events">Доставка событий</TabsTrigger>
      </TabsList>
      <TabsContent value="messages" className="space-y-4">
        {config && <>
          <div className="flex flex-wrap items-start justify-between gap-3 rounded-lg border px-4 py-3">
            <div className="max-w-xl space-y-1">
              <p className="text-sm font-medium">Ответы при ошибках подписки</p>
              <p className="text-xs text-muted-foreground">Сообщения используются для поддерживаемых клиентов с HWID. Настоящий HTTP-статус и проверка доступа не меняются для остальных форматов.</p>
            </div>
            <label className="flex items-center gap-2 text-sm">
              <Switch checked={config.enabled} disabled={!canUpdate || saving} onCheckedChange={enabled => updateSetting({ enabled })} />
              {config.enabled ? 'Включено' : 'Выключено'}
            </label>
          </div>
          <div className="grid min-w-0 gap-4 lg:grid-cols-[minmax(12rem,16rem)_minmax(0,1fr)]">
            <nav aria-label="Сценарии Firewall" className="min-w-0 space-y-1 rounded-lg border p-2">
              {FIREWALL_SCENARIO_KEYS.map(key => <button type="button" key={key}
                aria-current={key === scenario ? 'page' : undefined}
                onClick={() => changeScenario(key)}
                className={`w-full rounded-md px-3 py-3 text-left text-sm transition-colors focus-visible:ring-2 focus-visible:ring-ring ${key === scenario ? 'bg-primary/10 font-medium text-foreground' : 'text-muted-foreground hover:bg-muted hover:text-foreground'}`}>
                {FIREWALL_SCENARIOS[key]}
              </button>)}
            </nav>
            <section className="min-w-0 space-y-4 rounded-lg border p-4" aria-label="Редактор сценария">
              <div className="space-y-1">
                <h3 className="text-base font-semibold">{FIREWALL_SCENARIOS[scenario]}</h3>
                <p className="text-xs text-muted-foreground">Этот текст появится в подписочном клиенте при соответствующей ошибке, если формат поддерживается.</p>
              </div>
              <label className="block space-y-2 text-sm">
                <span>Заголовок</span>
                <Input disabled={!canUpdate || saving} aria-label="Заголовок сценария" value={template?.title ?? ''} maxLength={256}
                  onChange={event => updateTemplate('title', event.target.value)} />
              </label>
              <label className="block space-y-2 text-sm">
                <span>Информационные строки <span className="text-muted-foreground">(1–8, до 256 символов)</span></span>
                <Textarea disabled={!canUpdate || saving} aria-label="Строки сценария" rows={5} value={template?.lines.join('\n') ?? ''}
                  onChange={event => updateTemplate('lines', event.target.value)} />
              </label>
              {templateErrors.length > 0 && <p role="alert" className="text-sm text-destructive">{templateErrors.join(' ')}</p>}
              <div className="flex flex-wrap items-center justify-between gap-2">
                <p className="text-xs text-muted-foreground">Изменения сохраняются общей кнопкой вверху.</p>
                <Button type="button" variant="outline" disabled={templateErrors.length > 0} onClick={() => changeTab('preview')}>Открыть предпросмотр</Button>
              </div>
            </section>
          </div>
        </>}
        {!config && !loadError && <p className="text-sm text-muted-foreground">Загрузка сценариев…</p>}
      </TabsContent>
      <TabsContent value="preview" className="space-y-4">
        <section className="min-w-0 space-y-4 rounded-lg border p-4">
          <div className="space-y-1">
            <h3 className="text-base font-semibold">Предпросмотр ответа</h3>
            <p className="text-sm text-muted-foreground">Backend формирует Base64/announce из текущего черновика. Это не запрос к подписке реального пользователя и не изменение правил доступа.</p>
          </div>
          {config && <>
            <label className="block space-y-2 text-sm">
              <span>Сценарий</span>
              <select className="bg-background h-10 w-full rounded-md border px-3" value={scenario}
                aria-label="Сценарий предпросмотра" onChange={event => changeScenario(validFirewallScenario(event.target.value))}>
                {FIREWALL_SCENARIO_KEYS.map(key => <option value={key} key={key}>{FIREWALL_SCENARIOS[key]}</option>)}
              </select>
            </label>
            {templateErrors.length > 0 && <p role="alert" className="text-sm text-destructive">{templateErrors.join(' ')}</p>}
            <Button type="button" disabled={previewBusy || templateErrors.length > 0} onClick={() => void formPreview()}>
              {previewBusy ? 'Формирование…' : 'Сформировать предпросмотр'}
            </Button>
            <MessagePreview preview={preview} scenario={scenario} currentSignature={previewSignature} />
            <details className="text-xs text-muted-foreground">
              <summary className="cursor-pointer">Когда этот формат используется</summary>
              <p className="mt-2">Только для поддерживаемых клиентов текстовых подписок (например Happ, V2RayTun, ClientHub) с HWID. Для HTML, JSON, YAML и запросов без HWID обычный HTTP-ответ и контроль доступа сохраняются.</p>
            </details>
          </>}
        </section>
      </TabsContent>
      <TabsContent value="webhook" className="space-y-4">
        <section className="min-w-0 space-y-4 rounded-lg border p-4">
          <div className="space-y-1">
            <h3 className="text-base font-semibold">Webhook событий устройств</h3>
            <p className="text-sm text-muted-foreground">WayLane передаёт подписанные события HWID во внешний сервис. Секрет HMAC задаётся только на Backend и не отображается в этой форме.</p>
          </div>
          {config && <>
            <label className="flex items-center gap-3 text-sm">
              <Switch disabled={!canUpdate || saving} checked={config.webhook_enabled} onCheckedChange={webhook_enabled => updateSetting({ webhook_enabled })} />
              Отправлять события устройств
            </label>
            <label className="block space-y-2 text-sm">
              <span>URL получателя webhook</span>
              <Input disabled={!canUpdate || saving} aria-label="URL получателя webhook" type="url" autoComplete="off" placeholder="https://example.com/device-events"
                value={config.webhook_url ?? ''} maxLength={2048}
                onChange={event => updateSetting({ webhook_url: event.target.value || null })} />
            </label>
            <details className="space-y-3 text-sm">
              <summary className="cursor-pointer font-medium">Legacy-проверка устройств (дополнительно)</summary>
              <label className="mt-3 block space-y-2 text-sm">
                <span>URL `/webhook/check_device`</span>
                <Input disabled={!canUpdate || saving} aria-label="URL legacy-проверки устройства" type="url" autoComplete="off" placeholder="https://example.com/webhook/check_device"
                  value={config.device_check_url ?? ''} maxLength={2048}
                  onChange={event => updateSetting({ device_check_url: event.target.value || null })} />
              </label>
            </details>
            {validateWebhookFields(config).length > 0 && <p role="alert" className="text-sm text-destructive">{validateWebhookFields(config).join(' ')}</p>}
            <p className="text-xs text-muted-foreground">Сервер дополнительно проверяет HTTPS, внутренние адреса и наличие HMAC-секрета. HTTP для внутреннего получателя возможен только по серверному allowlist.</p>
            <p className="text-xs text-muted-foreground">Изменения сохраняются общей кнопкой вверху. Сохранение не подтверждает, что внешняя система получает события.</p>
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
          <AlertDialogTitle>Отменить несохранённые изменения?</AlertDialogTitle>
          <AlertDialogDescription>Повторная загрузка заменит черновик данными Backend. Сохранённые настройки не затронет.</AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel>Продолжить редактирование</AlertDialogCancel>
          <AlertDialogAction onClick={() => void loadConfig()}>Отменить изменения и загрузить</AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  </div>
}
