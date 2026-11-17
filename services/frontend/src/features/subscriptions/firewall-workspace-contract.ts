export const FIREWALL_SCENARIOS = {
  invalid_link: 'Ссылка на подписку недействительна',
  device_limit: 'Превышен лимит устройств',
  hwid_required: 'HWID отсутствует',
  unsupported_client: 'Приложение не поддерживается',
  rate_limited: 'Слишком частое обновление',
  access_denied: 'Доступ запрещён',
  bot_unavailable: 'Ошибка проверки устройства',
} as const

export type FirewallScenario = keyof typeof FIREWALL_SCENARIOS
export const FIREWALL_SCENARIO_KEYS = Object.keys(FIREWALL_SCENARIOS) as FirewallScenario[]
export type FirewallTab = 'messages' | 'preview' | 'webhook' | 'events'
export const FIREWALL_TABS = ['messages', 'preview', 'webhook', 'events'] as const

export type MessageTemplate = { title: string; lines: string[] }
export type FirewallEditor = {
  enabled: boolean
  scenarios: Record<FirewallScenario, MessageTemplate>
  webhook_enabled: boolean
  webhook_url: string | null
  device_check_url: string | null
}

export type DeviceEvent = {
  id: string
  event_type: string
  status: string
  attempts: number
  last_error: string | null
  next_attempt_at: string | null
  created_at: string
  delivered_at: string | null
}

export type DeviceEventSummary = Record<string, number>
export type PreviewResponse = { title: string; lines: string[]; base64_payload: string; announce: string }

export function validFirewallTab(value: string | null): FirewallTab {
  return FIREWALL_TABS.find(tab => tab === value) ?? 'messages'
}

export function validFirewallScenario(value: string | null): FirewallScenario {
  return FIREWALL_SCENARIO_KEYS.find(key => key === value) ?? 'invalid_link'
}

export type FirewallTranslate = (key: string, defaultValue: string) => string
const defaultTranslation: FirewallTranslate = (_key, fallback) => fallback

/** Mirrors the basic message constraints of Backend MessageTemplate. */
export function validateMessageTemplate(template: MessageTemplate, translate: FirewallTranslate = defaultTranslation): string[] {
  const problems: string[] = []
  if (!template.title.trim() || template.title.length > 256) problems.push(translate('validation.title', 'Заголовок: от 1 до 256 символов.'))
  if (template.lines.length < 1 || template.lines.length > 8) problems.push(translate('validation.linesCount', 'Информационные строки: от 1 до 8.'))
  if (template.lines.some(line => !line.trim() || line.length > 256)) problems.push(translate('validation.lineLength', 'Каждая информационная строка должна содержать от 1 до 256 непустых символов.'))
  return problems
}

/** Backend decides allowlisted internal hosts; UI only catches obvious format errors. */
export function validateWebhookFields(editor: FirewallEditor, translate: FirewallTranslate = defaultTranslation): string[] {
  const problems: string[] = []
  for (const [label, input] of [
    [translate('validation.webhookLabel', 'Webhook URL'), editor.webhook_url],
    [translate('validation.deviceCheckLabel', 'URL проверки устройства'), editor.device_check_url],
  ] as const) {
    if (!input) continue
    if (input.length > 2048) {
      problems.push(`${label}: ${translate('validation.urlMax', 'не более 2048 символов.')}`)
      continue
    }
    try {
      const url = new URL(input)
      if (!['https:', 'http:'].includes(url.protocol) || !url.hostname || url.username || url.password || url.hash) {
        problems.push(`${label}: ${translate('validation.urlFormat', 'требуется HTTP(S)-адрес без пароля, логина и якоря.')}`)
      }
    } catch {
      problems.push(`${label}: ${translate('validation.urlInvalid', 'некорректный URL.')}`)
    }
  }
  if (editor.webhook_enabled && !editor.webhook_url) problems.push(translate('validation.webhookRequired', 'Для отправки событий требуется URL webhook.'))
  return problems
}

export function validateFirewallDraft(editor: FirewallEditor, translate: FirewallTranslate = defaultTranslation): string[] {
  const messages = FIREWALL_SCENARIO_KEYS.flatMap(key => {
    const template = editor.scenarios[key]
    const label = translate(`scenario.${key}`, FIREWALL_SCENARIOS[key])
    return template ? validateMessageTemplate(template, translate).map(error => `${label}: ${error}`) : [`${translate('validation.missingScenario', 'Отсутствует сценарий')}: ${label}.`]
  })
  return [...messages, ...validateWebhookFields(editor, translate)]
}

export function firewallDraftChanged(saved: FirewallEditor | null, current: FirewallEditor | null): boolean {
  if (!saved || !current) return false
  return JSON.stringify(saved) !== JSON.stringify(current)
}

export function statusLabel(status: string): string {
  return ({
    pending: 'В очереди', sending: 'В обработке (последний статус)', failed: 'Ошибка — ожидает повтора',
    delivered: 'Доставлено', exhausted: 'Попытки исчерпаны',
  } as Record<string, string>)[status] ?? 'Неизвестное состояние'
}


export function deviceEventLabel(kind: string): string {
  return ({
    'device.added': 'Устройство добавлено',
    'device.updated': 'Устройство обновлено',
    'device.removed': 'Устройство удалено',
    'device.reset': 'Список устройств сброшен',
  } as Record<string, string>)[kind] ?? kind
}
