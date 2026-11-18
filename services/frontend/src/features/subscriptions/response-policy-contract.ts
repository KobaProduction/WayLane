export const POLICY_REASONS = [
  'client_blocked', 'commercial_denied', 'device_limit',
  'hwid_required', 'unsupported_client', 'invalid_link',
  'payment_required', 'subscription_expired', 'traffic_limit',
  'rate_limited', 'access_denied', 'external_device_limit',
  'external_access_denied', 'bot_unavailable',
] as const
export const POLICY_LANGUAGES = ['ru', 'en', 'fa', 'zh'] as const
export type PolicyLanguage = typeof POLICY_LANGUAGES[number]
export type PolicyReason = typeof POLICY_REASONS[number] | `external:${string}`
export type PolicyMode = 'http_error' | 'notice_200'
export type PolicyMatch = 'contains' | 'prefix' | 'exact'
export type ResponseRule = {
  id: string; name: string; ua_match: PolicyMatch; ua_value: string
  reason: PolicyReason; mode: PolicyMode
}
export type ResponseNotice = { title: string; lines: string[] }
export type ResponsePolicy = {
  enabled: boolean
  rules: ResponseRule[]
  notices: Record<string, Partial<Record<PolicyLanguage, ResponseNotice>>>
  external_denial_codes: string[]
}
export type ResponsePolicySnapshot = { revision: number; config: ResponsePolicy }
export type ResponsePreview = {
  decision: 'normal_subscription' | 'http_error' | 'notice_200'
  status: number; matched_rule_id: string | null; language: PolicyLanguage
  preview_title: string | null; preview_lines: string[]; wire_format: string | null; note: string
}
export const REASON_LABELS: Record<string, string> = {
  client_blocked: 'Заблокировано по приложению',
  commercial_denied: 'Коммерческий доступ запрещён (общая причина Core)',
  payment_required: 'Требуется оплата (подтверждённое состояние entitlement)',
  subscription_expired: 'Срок подписки истёк',
  traffic_limit: 'Исчерпан лимит трафика',
  device_limit: 'Превышен локальный лимит устройств',
  hwid_required: 'Нет обязательного HWID',
  unsupported_client: 'Приложение не поддерживается',
  invalid_link: 'Ссылка не найдена или отозвана',
  rate_limited: 'Превышена частота обновления',
  access_denied: 'Другой технический запрет',
  external_device_limit: 'Внешний сервис: лимит устройств',
  external_access_denied: 'Внешний сервис: доступ запрещён',
  bot_unavailable: 'Внешняя проверка недоступна (только будущий контракт)',
}
export function reasonLabel(reason: string): string {
  return reason.startsWith('external:')
    ? `Внешний код: ${reason.slice('external:'.length)}`
    : REASON_LABELS[reason] ?? reason
}
export const LANGUAGE_LABELS: Record<PolicyLanguage, string> = {
  ru: 'Русский', en: 'English', fa: 'فارسی', zh: '中文',
}
const EMPTY: ResponseNotice = { title: '', lines: [''] }

export function getNotice(policy: ResponsePolicy, reason: string, language: PolicyLanguage): ResponseNotice {
  return policy.notices[reason]?.[language] ?? { ...EMPTY, lines: [...EMPTY.lines] }
}

export function withNotice(policy: ResponsePolicy, reason: string, language: PolicyLanguage, notice: ResponseNotice): ResponsePolicy {
  return { ...policy, notices: { ...policy.notices,
    [reason]: { ...(policy.notices[reason] ?? {}), [language]: notice },
  } }
}

/** Do not accidentally treat an HTTP 200 as an authorized VPN configuration. */
export function previewIsExplicitlyInert(preview: ResponsePreview): boolean {
  return preview.decision === 'notice_200' && preview.status === 200 &&
    (preview.wire_format === 'links' || preview.wire_format === 'links_base64')
}

export type PolicyValidationTranslation = (key: string, fallback: string) => string

export function responsePolicyErrors(
  policy: ResponsePolicy,
  translate: PolicyValidationTranslation = (_key, fallback) => fallback,
): string[] {
  const errors: string[] = []
  const ids = new Set<string>()
  for (const [index, rule] of policy.rules.entries()) {
    if (!rule.id || ids.has(rule.id)) errors.push(`${translate('duplicateId', 'Повтор ID правила')} ${index + 1}`)
    ids.add(rule.id)
    if (!rule.name.trim() || rule.name.length > 80) errors.push(`${translate('rule', 'Правило')} ${index + 1}: ${translate('name', 'название 1–80 символов')}`)
    if (rule.ua_value.length > 128) errors.push(`${translate('rule', 'Правило')} ${index + 1}: ${translate('uaTooLong', 'User-Agent слишком длинный')}`)
    if (rule.mode === 'notice_200') {
      const texts = Object.values(policy.notices[rule.reason] ?? {})
      if (!texts.length || !texts.some(t => t?.title.trim() && t.lines.length && t.lines.every(l => l.trim()))) {
        errors.push(`${translate('rule', 'Правило')} ${index + 1}: ${translate('missingNotice', 'заполните текст информационного ответа')}`)
      }
    }
  }
  for (const [reason, texts] of Object.entries(policy.notices)) {
    for (const [lang, notice] of Object.entries(texts)) {
      if (!notice || !notice.title.trim() || notice.title.length > 256 || notice.lines.length < 1 || notice.lines.length > 8 || notice.lines.some(line => !line.trim() || line.length > 256)) {
        errors.push(`${translate('template', 'Шаблон')} ${reason}/${lang}: ${translate('invalidNotice', 'заголовок и 1–8 непустых строк, до 256 символов')}`)
      }
    }
  }
  if (policy.external_denial_codes.some(code => !/^[A-Z][A-Z0-9_]{0,63}$/.test(code))) errors.push(translate('invalidCode', 'Некорректный внешний код'))
  if (policy.external_denial_codes.some(code => ['ALLOW', 'OK', 'BOT_ERROR', 'ACCESS_GRANTED'].includes(code))) errors.push(translate('unsafeCode', 'Коды успешного допуска/сбоя не могут означать отказ'))
  return errors
}
