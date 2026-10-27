import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Switch } from '@/components/ui/switch'
import {
  AMNEZIA_31_PRESET,
  AWG_BOOLEAN_FIELDS,
  AWG_CPS_FIELDS,
  AWG_NUMBER_FIELDS,
  AWG_RANGE_FIELDS,
  applyAmneziaPreset,
  getAmneziaSettings,
  isAmneziaDraft,
  setAmneziaMode,
  updateAmneziaSetting,
} from '@/features/core-editor/kit/amneziawg-settings'
import type { AmneziaField } from '@/features/core-editor/kit/amneziawg-settings'
import type { WireGuardCoreDraft } from '@pasarguard/wireguard-config-kit'
import { useId } from 'react'
import { useTranslation } from 'react-i18next'

const fieldLabels: Record<AmneziaField, string> = {
  jc: 'Jc', jmin: 'Jmin', jmax: 'Jmax',
  s1: 'S1', s2: 'S2', s3: 'S3', s4: 'S4',
  h1: 'H1', h2: 'H2', h3: 'H3', h4: 'H4',
  i1: 'I1', i2: 'I2', i3: 'I3', i4: 'I4', i5: 'I5',
  header_protection_key: 'HeaderProtectionKey',
  content_padding_addition: 'ContentPaddingAddition',
  rekey_after_time: 'RekeyAfterTime',
  rekey_timeout: 'RekeyTimeout',
  reject_after_time: 'RejectAfterTime',
  keepalive_timeout: 'KeepaliveTimeout',
  max_handshake_attempts: 'MaxHandshakeAttempts',
  random_trailers: 'RandomTrailers',
  disable_cookies: 'DisableCookies',
}

export function AmneziaWGSettingsForm({
  draft,
  updateDraft,
}: {
  draft: WireGuardCoreDraft
  updateDraft: (updater: (draft: WireGuardCoreDraft) => WireGuardCoreDraft) => void
}) {
  const { t } = useTranslation()
  const baseId = useId()
  const isAmnezia = isAmneziaDraft(draft)
  const settings = getAmneziaSettings(draft)

  const option = (field: AmneziaField, description?: string) => {
    const id = baseId + '-' + field
    return (
      <div key={field} className="min-w-0 space-y-1.5">
        <Label htmlFor={id}>{fieldLabels[field]}</Label>
        <Input
          id={id}
          aria-label={fieldLabels[field]}
          dir="ltr"
          className="min-w-0 font-mono text-xs"
          value={String(settings[field] ?? '')}
          placeholder={(AMNEZIA_31_PRESET[field] ?? '').toString()}
          onChange={e => updateDraft(current => updateAmneziaSetting(current, field, e.target.value))}
          autoComplete="off"
        />
        {description && <p className="text-muted-foreground text-xs">{description}</p>}
      </div>
    )
  }

  return (
    <section className="sm:col-span-2 space-y-4 rounded-lg border p-3 sm:p-4" aria-label="WireGuard transport mode">
      <div className="space-y-1">
        <Label htmlFor={baseId + '-engine'}>{t('coreEditor.awg.engine', { defaultValue: 'WireGuard transport' })}</Label>
        <select
          id={baseId + '-engine'}
          className="border-input bg-background focus-visible:ring-ring h-10 w-full rounded-md border px-3 text-sm focus-visible:ring-2 focus-visible:outline-none"
          value={isAmnezia ? 'amneziawg' : 'wireguard'}
          onChange={e => updateDraft(current => setAmneziaMode(current, e.target.value === 'amneziawg'))}
        >
          <option value="wireguard">{t('coreEditor.awg.plain', { defaultValue: 'WireGuard (standard clients)' })}</option>
          <option value="amneziawg">{t('coreEditor.awg.obfuscated', { defaultValue: 'AmneziaWG 3.1 (obfuscation)' })}</option>
        </select>
      </div>

      {isAmnezia && (
        <div className="space-y-4">
          <p className="text-muted-foreground text-sm">
            {t('coreEditor.awg.compatibility', {
              defaultValue: 'Requires WayLane Node 0.6.0+ and an AmneziaWG-compatible client with matching settings. Standard WireGuard clients work only when obfuscation is disabled. Host VPN traffic is not yet acceptance-tested.',
            })}
          </p>
          <Button
            type="button"
            variant="outline"
            className="w-full sm:w-auto"
            onClick={() => updateDraft(applyAmneziaPreset)}
          >
            {t('coreEditor.awg.preset', { defaultValue: 'Apply AmneziaWG 3.1 preset' })}
          </Button>
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            {AWG_NUMBER_FIELDS.map(field => option(field))}
          </div>
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            {AWG_RANGE_FIELDS.slice(0, 4).map(field => option(field))}
          </div>
          <details className="space-y-3 rounded-md border p-3">
            <summary className="cursor-pointer text-sm font-medium">
              {t('coreEditor.awg.advanced', { defaultValue: 'Advanced AWG 3.1 options' })}
            </summary>
            <div className="grid grid-cols-1 gap-3 pt-3 sm:grid-cols-2">
              {AWG_CPS_FIELDS.map(field => option(field))}
              {AWG_RANGE_FIELDS.slice(4).map(field => option(field))}
              {option('header_protection_key', t('coreEditor.awg.headerKeyHint', { defaultValue: 'Base64, 32 bytes. All S1–S4 must be at least 12 when this is set.' }))}
            </div>
          </details>
          <div className="grid gap-3 sm:grid-cols-2">
            {AWG_BOOLEAN_FIELDS.map(field => (
              <div key={field} className="flex min-w-0 items-center justify-between gap-3 rounded-md border p-3">
                <Label htmlFor={baseId + '-' + field} className="min-w-0 break-words">{fieldLabels[field]}</Label>
                <Switch
                  id={baseId + '-' + field}
                  checked={settings[field] === true}
                  onCheckedChange={checked => updateDraft(current => updateAmneziaSetting(current, field, checked))}
                />
              </div>
            ))}
          </div>
        </div>
      )}
    </section>
  )
}
