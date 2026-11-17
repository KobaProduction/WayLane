import { Badge } from '@/components/ui/badge'
import { useTranslation } from 'react-i18next'
import type { XrayStatus } from './node-xray-types'

export type XrayApplyState = 'unloaded' | 'unconfigured' | 'queued' | 'applied' | 'stopped' | 'error' | 'requires_restart' | 'unknown'

/** Generation and Node report must agree before the UI can claim application. */
export function xrayApplyState(state: XrayStatus | undefined): XrayApplyState {
  if (!state) return 'unloaded'
  if (!state.configured) return 'unconfigured'
  if (state.runtime_status === 'error') return 'error'
  if (state.runtime_status === 'requires_restart') return 'requires_restart'
  const desired = state.generation
  const applied = state.applied_generation
  if (state.runtime_status === 'pending') return 'queued'
  if (desired == null || applied == null || desired <= 0 || applied < 0 || applied > desired) return 'unknown'
  if (applied < desired) return 'queued'
  if (state.runtime_status === 'running') return 'applied'
  if (state.runtime_status === 'stopped') return 'stopped'
  return 'unknown'
}

const labels: Record<XrayApplyState, string> = {
  unloaded: 'Статус Xray не загружен',
  unconfigured: 'Xray не настроен',
  queued: 'Ожидается подтверждение Node',
  applied: 'Node подтвердила применение',
  stopped: 'Node подтвердила остановку',
  error: 'Node сообщила об ошибке',
  requires_restart: 'Требуется перезапуск Xray',
  unknown: 'Применение не подтверждено',
}

export function NodeXrayApplyStatus({ current }: { current: XrayStatus | undefined }) {
  const { t } = useTranslation()
  const result = xrayApplyState(current)
  const variant = result === 'error' ? 'red' : result === 'applied' ? 'green' : 'outline'
  return <div className="space-y-1 text-xs" role="status" aria-label="Применение конфигурации Xray">
    <div className="flex flex-wrap items-center gap-2">
      <Badge variant={variant}>{t(`nodes.xrayApply.${result}`, { defaultValue: labels[result] })}</Badge>
      {current?.configured && <span className="text-muted-foreground tabular-nums">
        {t('nodes.xrayApply.appliedGeneration', { defaultValue: 'Применено' })}: {current.applied_generation ?? '—'} · {t('nodes.xrayApply.desiredGeneration', { defaultValue: 'желаемое' })}: {current.generation ?? '—'}
      </span>}
    </div>
    {current?.reported_at && <p className="text-muted-foreground">{t('nodes.xrayApply.lastReport', { defaultValue: 'Последний отчёт Xray' })}: {new Date(current.reported_at).toLocaleString()}</p>}
    {result === 'error' && current?.last_error && <p role="alert" className="break-all text-destructive">{t('nodes.xrayApply.errorLabel', { defaultValue: 'Ошибка' })}: {current.last_error}</p>}
  </div>
}
