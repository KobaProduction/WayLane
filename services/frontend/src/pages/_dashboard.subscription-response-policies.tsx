import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link, useSearchParams } from 'react-router'
import { toast } from 'sonner'
import { useTranslation } from 'react-i18next'
import { useAdmin } from '@/hooks/use-admin'
import { hasPermission } from '@/utils/rbac'
import { $fetch } from '@/service/http'
import PageHeader from '@/components/layout/page-header'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'
import { Badge } from '@/components/ui/badge'
import { Switch } from '@/components/ui/switch'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import {
  AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent,
  AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle,
} from '@/components/ui/alert-dialog'
import {
  LANGUAGE_LABELS, POLICY_LANGUAGES, POLICY_REASONS,
  getNotice, withNotice, responsePolicyErrors, previewIsExplicitlyInert,
} from '@/features/subscriptions/response-policy-contract'
import type {
  PolicyLanguage, PolicyReason, ResponsePolicy, ResponsePolicySnapshot,
  ResponseRule, ResponsePreview,
} from '@/features/subscriptions/response-policy-contract'

const endpoint = '/api/subscription-response-policies'
const TABS = ['rules', 'messages', 'external', 'preview'] as const
const EMPTY: ResponsePolicy = { enabled: false, rules: [], notices: {}, external_denial_codes: [] }
type Tab = typeof TABS[number]
const formats = ['links_base64', 'links', 'clash', 'xray', 'sing_box'] as const

function failureKey(error: unknown): string {
  const e = error as { status?: number }
  if (e.status === 409) return 'errorConflict'
  if (e.status === 422) return 'errorValidation'
  return 'errorGeneral'
}

export default function SubscriptionResponsePoliciesPage() {
  const { admin } = useAdmin()
  const { t, i18n } = useTranslation()
  const tc = (key: string) => t(`subscriptionResponsePolicy.ui.${key}`)
  const describeReason = (value: string) => value.startsWith('external:')
    ? `${tc('reason.externalCode')}: ${value.slice('external:'.length)}`
    : tc(`reason.${value}`)
  const canRead = hasPermission(admin, 'settings', 'read')
  const canUpdate = hasPermission(admin, 'settings', 'update')
  const [params, setParams] = useSearchParams()
  const tab = TABS.includes(params.get('tab') as Tab) ? params.get('tab') as Tab : 'rules'
  const urlLanguage = POLICY_LANGUAGES.includes(params.get('lang') as PolicyLanguage) ? params.get('lang') as PolicyLanguage : 'ru'
  const [language, setLanguage] = useState<PolicyLanguage>(urlLanguage)
  const editingLanguageRef = useRef<PolicyLanguage>(urlLanguage)
  useEffect(() => {
    if (editingLanguageRef.current !== urlLanguage) {
      editingLanguageRef.current = urlLanguage
      setLanguage(urlLanguage)
    }
  }, [urlLanguage])
  const [saved, setSaved] = useState<ResponsePolicySnapshot | null>(null)
  const [draft, setDraft] = useState<ResponsePolicy>(EMPTY)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const [conflict, setConflict] = useState(false)
  const [confirmDiscard, setConfirmDiscard] = useState(false)
  const [confirmEnable, setConfirmEnable] = useState(false)
  const [editedReason, setEditedReason] = useState<PolicyReason>('device_limit')
  const [newExternalCode, setNewExternalCode] = useState('')
  const [previewReason, setPreviewReason] = useState<PolicyReason>('device_limit')
  const [previewAgent, setPreviewAgent] = useState('Happ/2.0')
  const [previewAccept, setPreviewAccept] = useState('*/*')
  const [previewFormat, setPreviewFormat] = useState<(typeof formats)[number]>('links_base64')
  const [preview, setPreview] = useState<{ data: ResponsePreview; signature: string } | null>(null)
  const [previewPending, setPreviewPending] = useState(false)

  const dirty = saved !== null && JSON.stringify(saved.config) !== JSON.stringify(draft)
  const validation = useMemo(() => responsePolicyErrors(draft, (key, fallback) => t(
    `subscriptionResponsePolicy.ui.validation.${key}`, { defaultValue: fallback },
  )), [draft, t])
  const reasons = useMemo(() => [
    ...POLICY_REASONS,
    ...draft.external_denial_codes.map(code => `external:${code}` as const),
  ], [draft.external_denial_codes])
  const selectedNotice = getNotice(draft, editedReason, language)
  const previewSignature = JSON.stringify({ draft, previewAgent, previewAccept, previewFormat, previewReason, language })

  const fetchCurrent = useCallback(async () => {
    if (!canRead) return
    setLoading(true)
    try {
      const response = await $fetch<ResponsePolicySnapshot>(endpoint)
      setDraft(response.config)
      setSaved(response)
      setConflict(false)
      setError('')
      setPreview(null)
    } catch (reason) { setError(failureKey(reason)) }
    finally { setLoading(false) }
  }, [canRead])
  useEffect(() => { void fetchCurrent() }, [fetchCurrent])

  function navigate(nextTab: Tab, nextLanguage = language) {
    editingLanguageRef.current = nextLanguage
    setLanguage(nextLanguage)
    const query = new URLSearchParams(params)
    if (nextTab === 'rules') query.delete('tab')
    else query.set('tab', nextTab)
    if (nextLanguage === 'ru') query.delete('lang')
    else query.set('lang', nextLanguage)
    setParams(query)
  }

  function updateRule(index: number, patch: Partial<ResponseRule>) {
    setDraft(current => ({ ...current, rules: current.rules.map((rule, i) => i === index ? { ...rule, ...patch } : rule) }))
    setPreview(null)
  }

  function addRule() {
    setDraft(current => ({ ...current, rules: [...current.rules, {
      id: 'rule-' + (globalThis.crypto?.randomUUID?.() ?? `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`), name: tc('addRuleName'), ua_match: 'contains',
      ua_value: '', reason: 'device_limit', mode: 'http_error',
    }] }))
    setPreview(null)
  }

  function addExternalCode() {
    const code = newExternalCode.trim().toUpperCase()
    if (!/^[A-Z][A-Z0-9_]{0,63}$/.test(code) || ['ALLOW','OK','BOT_ERROR','ACCESS_GRANTED'].includes(code)) {
      toast.error(tc('invalidCode'))
      return
    }
    setDraft(current => ({ ...current, external_denial_codes: [...new Set([...current.external_denial_codes, code])] }))
    setNewExternalCode('')
  }

  function changeNotice(field: 'title' | 'lines', value: string) {
    setDraft(current => {
      const lang = editingLanguageRef.current
      const previous = getNotice(current, editedReason, lang)
      const next = { ...previous, [field]: field === 'lines' ? value.split('\n') : value }
      return withNotice(current, editedReason, lang, next)
    })
    setPreview(null)
  }

  async function save() {
    if (!canUpdate || !saved || !dirty || saving || conflict || validation.length) return
    setSaving(true)
    try {
      const result = await $fetch<ResponsePolicySnapshot>(endpoint, {
        method: 'PUT', body: { revision: saved.revision, config: draft },
      })
      setDraft(result.config)
      setSaved(result)
      setError('')
      setConflict(false)
      toast.success(tc('toastSaved'))
    } catch (reason) {
      const e = reason as { status?: number }
      setError(failureKey(reason))
      if (e.status === 409) setConflict(true)
    } finally { setSaving(false) }
  }

  async function runPreview() {
    if (validation.length || previewPending || !canRead) return
    setPreviewPending(true)
    const signature = previewSignature
    try {
      const result = await $fetch<ResponsePreview>(endpoint + '/preview', {
        method: 'POST', body: {
          config: draft, user_agent: previewAgent, accept: previewAccept,
          accept_language: language, client_format: previewFormat,
          reason: previewReason,
        },
      })
      setPreview({ data: result, signature })
    } catch (reason) { toast.error(tc(failureKey(reason))) }
    finally { setPreviewPending(false) }
  }

  if (!canRead) return <p className="p-4 text-sm text-muted-foreground" role="alert">{tc('noAccess')}</p>
  return <div className="min-w-0 space-y-4" data-testid="subscription-response-policies">
    <PageHeader title="subscriptionResponsePolicy.title" description="subscriptionResponsePolicy.description" />
    <div className="space-y-4 px-4 pb-6">
      <nav className="flex flex-wrap items-center gap-3 text-sm" aria-label={tc('subsLink')}>
        <Link className="text-primary underline underline-offset-4" to="/subscriptions">{tc('subsLink')}</Link>
        <strong>{tc('policiesLink')}</strong>
        <Link className="text-primary underline underline-offset-4" to="/subscriptions/devices?tab=events">{tc('devicesLink')}</Link>
        <Link className="text-primary underline underline-offset-4" to="/settings/subscriptions">{tc('formatsLink')}</Link>
      </nav>
      <div className="flex flex-wrap items-start justify-between gap-3 rounded-lg border p-4">
        <div className="space-y-1">
          <p className="font-semibold">{tc('behaviorTitle')}</p>
          <p className="max-w-2xl text-sm text-muted-foreground">{tc('behaviorDesc')}</p>
          <p className="text-xs text-muted-foreground" role="status">{loading && !saved ? tc('loading') : dirty ? tc('unsaved') : tc('saved')} · {tc('revision')} {saved?.revision ?? '—'}</p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Button type="button" size="sm" variant="outline" disabled={loading || saving} onClick={() => dirty ? setConfirmDiscard(true) : void fetchCurrent()}>{conflict ? tc('loadServer') : tc('refresh')}</Button>
          {canUpdate && <Button type="button" size="sm" disabled={!dirty || saving || loading || conflict || validation.length > 0} onClick={() => draft.enabled && !saved?.config.enabled ? setConfirmEnable(true) : void save()}>{saving ? tc('saving') : tc('save')}</Button>}
        </div>
      </div>
      {error && <p role="alert" className="rounded-lg border border-destructive/50 p-3 text-sm text-destructive">{tc(error)} {conflict && tc('conflictDraft')}</p>}
      {validation.length > 0 && <div role="alert" className="rounded-lg border border-destructive/50 p-3 text-sm text-destructive"><p className="font-semibold">{tc('validationTitle')}</p><ul className="mt-2 list-disc space-y-1 pl-5">{validation.map((error, i) => <li key={i}>{error}</li>)}</ul></div>}
      {saved && <>
        <section className="flex flex-wrap items-center justify-between gap-3 rounded-lg border p-4">
          <div className="space-y-1"><h2 className="font-medium">{tc('policyTitle')}</h2><p className="text-xs text-muted-foreground">{tc('policyDesc')}</p></div>
          <label className="flex items-center gap-3 text-sm"><Switch checked={draft.enabled} disabled={!canUpdate} onCheckedChange={enabled => {setDraft(current => ({...current,enabled}));setPreview(null)}} />{draft.enabled ? tc('enabled') : tc('disabled')}</label>
        </section>
        <Tabs value={tab} onValueChange={value => navigate(value as Tab)} className="space-y-4">
          <TabsList className="h-auto w-full flex-wrap justify-start gap-1" aria-label={tc('tabsLabel')}>
            <TabsTrigger value="rules">{tc('rulesTab')}</TabsTrigger>
            <TabsTrigger value="messages">{tc('messagesTab')}</TabsTrigger>
            <TabsTrigger value="external">{tc('externalTab')}</TabsTrigger>
            <TabsTrigger value="preview">{tc('previewTab')}</TabsTrigger>
          </TabsList>
          <TabsContent value="rules" className="space-y-3">
            <div className="flex flex-wrap items-center justify-between gap-2"><div className="space-y-1"><h3 className="font-semibold">{tc('rulesTitle')}</h3><p className="text-sm text-muted-foreground">{tc('rulesDesc')}</p></div>{canUpdate && <Button size="sm" onClick={addRule}>{tc('addRule')}</Button>}</div>
            {draft.rules.length === 0 && <p role="status" className="rounded-lg border border-dashed p-6 text-sm text-muted-foreground">{tc('rulesEmpty')}</p>}
            <div className="space-y-3">
              {draft.rules.map((rule, index) => <section key={rule.id} className="min-w-0 space-y-3 rounded-lg border p-4" aria-label={`${tc('ruleLabel')} ${index+1}`}>
                <div className="flex flex-wrap items-center justify-between gap-2"><h4 className="text-sm font-semibold">{tc('ruleLabel')} #{index+1}</h4><div className="flex flex-wrap gap-2">{index>0 && <Button size="sm" variant="ghost" disabled={!canUpdate} onClick={()=>setDraft(v=>{const rules=[...v.rules];[rules[index-1],rules[index]]=[rules[index],rules[index-1]];return {...v,rules}})}>{tc('moveUp')}</Button>}{index<draft.rules.length-1 && <Button size="sm" variant="ghost" disabled={!canUpdate} onClick={()=>setDraft(v=>{const rules=[...v.rules];[rules[index+1],rules[index]]=[rules[index],rules[index+1]];return {...v,rules}})}>{tc('moveDown')}</Button>}{canUpdate && <Button size="sm" variant="outline" onClick={()=>setDraft(v=>({...v,rules:v.rules.filter(r=>r.id!==rule.id)}))}>{tc('remove')}</Button>}</div></div>
                <label className="block space-y-1 text-sm"><span>{tc('name')}</span><Input aria-label={`${tc('name')} ${index+1}`} disabled={!canUpdate} value={rule.name} maxLength={80} onChange={e=>updateRule(index,{name:e.target.value})} /></label>
                <div className="grid gap-3 sm:grid-cols-[minmax(8rem,12rem)_minmax(0,1fr)]">
                  <label className="block space-y-1 text-sm"><span>{tc('matchType')}</span><select className="bg-background h-10 w-full rounded-md border px-3" disabled={!canUpdate} value={rule.ua_match} onChange={e=>updateRule(index,{ua_match:e.target.value as ResponseRule['ua_match']})}><option value="contains">{tc('contains')}</option><option value="prefix">{tc('prefix')}</option><option value="exact">{tc('exact')}</option></select></label>
                  <label className="block space-y-1 text-sm"><span>{tc('uaLabel')}</span><Input aria-label={`User-Agent ${index+1}`} disabled={!canUpdate} value={rule.ua_value} maxLength={128} placeholder="Happ / 2.0" onChange={e=>updateRule(index,{ua_value:e.target.value})} /></label>
                </div>
                <div className="grid gap-3 sm:grid-cols-2">
                  <label className="block space-y-1 text-sm"><span>{tc('reasonLabel')}</span><select aria-label={`${tc('reasonLabel')} ${index+1}`} className="bg-background h-10 w-full rounded-md border px-3" disabled={!canUpdate} value={rule.reason} onChange={e=>updateRule(index,{reason:e.target.value as PolicyReason})}>{reasons.map(reason=><option key={reason} value={reason}>{describeReason(reason)}</option>)}</select></label>
                  <label className="block space-y-1 text-sm"><span>{tc('responseLabel')}</span><select aria-label={`${tc('responseLabel')} ${index+1}`} className="bg-background h-10 w-full rounded-md border px-3" disabled={!canUpdate} value={rule.mode} onChange={e=>updateRule(index,{mode:e.target.value as ResponseRule['mode']})}><option value="http_error">{tc('httpError')}</option><option value="notice_200">{tc('notice200')}</option></select></label>
                </div>
                {rule.reason==='client_blocked' && <p className="text-xs text-muted-foreground">{tc('blockedNote')}</p>}
                {rule.mode==='notice_200' && <div className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-dashed p-3 text-xs"><p className="max-w-xl text-muted-foreground">{tc('noticeHelp')}</p><Button type="button" size="sm" variant="outline" onClick={()=>{setEditedReason(rule.reason);navigate('messages')}}>{tc('editMessages')}</Button></div>}
              </section>)}
            </div>
          </TabsContent>
          <TabsContent value="messages" className="space-y-4">
            <section className="space-y-3 rounded-lg border p-4">
              <h3 className="font-semibold">{tc('messagesTitle')}</h3>
              <p className="text-sm text-muted-foreground">{tc('messagesDesc')}</p>
              <div className="grid gap-3 sm:grid-cols-2"><label className="block space-y-1 text-sm"><span>{tc('reasonLabel')}</span><select aria-label={tc('noticeReason')} className="bg-background h-10 w-full rounded-md border px-3" value={reasons.includes(editedReason)?editedReason:'device_limit'} onChange={e=>setEditedReason(e.target.value as PolicyReason)}>{reasons.map(reason=><option key={reason} value={reason}>{describeReason(reason)}</option>)}</select></label><label className="block space-y-1 text-sm"><span>{tc('noticeLanguage')}</span><select aria-label={tc('noticeLanguage')} className="bg-background h-10 w-full rounded-md border px-3" value={language} onChange={e=>navigate(tab,e.target.value as PolicyLanguage)}>{POLICY_LANGUAGES.map(lang=><option key={lang} value={lang}>{LANGUAGE_LABELS[lang]}</option>)}</select></label></div>
              {draft.notices[editedReason]?.[language] ? <Badge variant="outline">{tc('noticePresent')}</Badge> : <Badge variant="outline">{tc('noticeMissing')}</Badge>}
              <label className="block space-y-1 text-sm"><span>{tc('subject')}</span><Input aria-label={tc('subject')} disabled={!canUpdate} maxLength={256} value={selectedNotice.title} onChange={e=>changeNotice('title',e.target.value)} /></label>
              <label className="block space-y-1 text-sm"><span>{tc('lines')}</span><Textarea aria-label={tc('lines')} disabled={!canUpdate} rows={6} value={selectedNotice.lines.join('\n')} onChange={e=>changeNotice('lines',e.target.value)} /></label>
              <p className="text-xs text-muted-foreground">{tc('noticeSecurity')}</p>
              {canUpdate && <div className="flex flex-wrap gap-2"><Button type="button" size="sm" variant="outline" onClick={()=>setDraft(v=>withNotice(v,editedReason,language,{title:describeReason(editedReason),lines:[i18n.getFixedT(language)('subscriptionResponsePolicy.ui.defaultNotice')]}))}>{tc('safeFill')}</Button>{draft.notices[editedReason]?.[language] && <Button type="button" size="sm" variant="ghost" onClick={()=>setDraft(v=>{const notices={...v.notices};const translations={...(notices[editedReason]??{})};delete translations[language];if(Object.keys(translations).length)notices[editedReason]=translations;else delete notices[editedReason];return {...v,notices}})}>{tc('deleteTranslation')}</Button>}</div>}
            </section>
          </TabsContent>
          <TabsContent value="external" className="space-y-4">
            <section className="space-y-3 rounded-lg border p-4"><h3 className="font-semibold">{tc('externalTitle')}</h3><p className="text-sm text-muted-foreground">{tc('externalDesc')}</p><div className="flex flex-wrap gap-2">{draft.external_denial_codes.map(code=><div key={code} className="flex items-center gap-2 rounded-md border px-3 py-2"><code className="break-all text-xs">{code}</code>{canUpdate && <Button variant="ghost" size="sm" onClick={()=>{if(draft.rules.some(rule=>rule.reason===`external:${code}`)){toast.error(tc('codeInUse'));return}setDraft(v=>({...v,external_denial_codes:v.external_denial_codes.filter(item=>item!==code)}))}}>{tc('remove')}</Button>}</div>)}</div>{canUpdate && <div className="flex flex-wrap gap-2"><Input className="min-w-0 flex-1" aria-label={tc('codeLabel')} placeholder="PAYMENT_REQUIRED" value={newExternalCode} maxLength={64} onChange={e=>setNewExternalCode(e.target.value)} /><Button type="button" variant="outline" onClick={addExternalCode}>{tc('addCode')}</Button></div>}<p className="text-xs text-muted-foreground">{tc('externalWarning')}</p></section>
          </TabsContent>
          <TabsContent value="preview" className="space-y-4">
            <section className="space-y-3 rounded-lg border p-4"><h3 className="font-semibold">{tc('previewTitle')}</h3><p className="text-sm text-muted-foreground">{tc('previewDesc')}</p>
              <div className="grid gap-3 sm:grid-cols-2"><label className="block space-y-1 text-sm"><span>User-Agent</span><Input aria-label={tc('previewUserAgent')} value={previewAgent} maxLength={512} onChange={e=>setPreviewAgent(e.target.value)} /></label><label className="block space-y-1 text-sm"><span>{tc('previewReason')}</span><select aria-label={tc('previewReason')} value={reasons.includes(previewReason)?previewReason:'device_limit'} className="bg-background h-10 w-full rounded-md border px-3" onChange={e=>setPreviewReason(e.target.value as PolicyReason)}>{reasons.map(reason=><option key={reason} value={reason}>{describeReason(reason)}</option>)}</select></label><label className="block space-y-1 text-sm"><span>{tc('previewFormat')}</span><select aria-label={tc('previewFormat')} className="bg-background h-10 w-full rounded-md border px-3" value={previewFormat} onChange={e=>setPreviewFormat(e.target.value as typeof previewFormat)}>{formats.map(value=><option key={value} value={value}>{tc(`format.${value}`)}</option>)}</select></label><label className="block space-y-1 text-sm"><span>HTTP Accept</span><Input aria-label={tc('previewAccept')} value={previewAccept} maxLength={512} onChange={e=>setPreviewAccept(e.target.value)} /></label></div>
              <Button disabled={previewPending || validation.length>0} onClick={()=>void runPreview()}>{previewPending ? tc('previewRunning') : tc('previewRun')}</Button>
              {preview && preview.signature !== previewSignature && <p role="status" className="text-sm text-muted-foreground">{tc('previewStale')}</p>}
              {preview && preview.signature === previewSignature && <div role="status" className="space-y-2 rounded-lg border p-4" data-testid="response-policy-preview-result"><div className="flex flex-wrap items-center gap-2"><Badge variant="outline">HTTP {preview.data.status}</Badge><Badge variant="outline">{preview.data.decision === 'notice_200' ? tc('previewNotice') : preview.data.decision === 'normal_subscription' ? tc('previewNormal') : tc('previewDenied')}</Badge></div><p className="text-sm text-muted-foreground">{tc('matchedRule')}: {draft.rules.find(rule=>rule.id===preview.data.matched_rule_id)?.name ?? tc('noMatch')} · {tc('previewLanguage')}: {LANGUAGE_LABELS[preview.data.language]}</p>{previewIsExplicitlyInert(preview.data) && <div className="space-y-2"><p className="font-medium">{preview.data.preview_title}</p>{preview.data.preview_lines.map((line,index)=><p key={index} className="break-words text-sm">{line}</p>)}<p className="text-xs text-muted-foreground">{tc('previewInert')}</p></div>}</div>}
            </section>
          </TabsContent>
        </Tabs>
      </>}
    </div>
    <AlertDialog open={confirmDiscard} onOpenChange={setConfirmDiscard}><AlertDialogContent><AlertDialogHeader><AlertDialogTitle>{tc('discardTitle')}</AlertDialogTitle><AlertDialogDescription>{tc('discardDesc')}</AlertDialogDescription></AlertDialogHeader><AlertDialogFooter><AlertDialogCancel>{tc('keepEditing')}</AlertDialogCancel><AlertDialogAction onClick={()=>void fetchCurrent()}>{tc('loadServer')}</AlertDialogAction></AlertDialogFooter></AlertDialogContent></AlertDialog>
    <AlertDialog open={confirmEnable} onOpenChange={setConfirmEnable}><AlertDialogContent><AlertDialogHeader><AlertDialogTitle>{tc('confirmEnableTitle')}</AlertDialogTitle><AlertDialogDescription>{tc('confirmEnableDesc')}</AlertDialogDescription></AlertDialogHeader><AlertDialogFooter><AlertDialogCancel>{tc('cancel')}</AlertDialogCancel><AlertDialogAction onClick={()=>void save()}>{tc('confirmEnable')}</AlertDialogAction></AlertDialogFooter></AlertDialogContent></AlertDialog>
  </div>
}
