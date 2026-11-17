import { Link } from 'react-router'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'

export type AgentTLSBinding = {
  inbound_tag: string
  preset_id: string
  preset_name: string
  revision: number
  fingerprint_sha256: string
  not_after: string
}

export type TLSPresetSummary = {
  id: string
  name: string
  revision: number
  fingerprint_sha256: string
}

type Props = {
  bindings: AgentTLSBinding[]
  presets: TLSPresetSummary[]
  inboundTag: string
  onInboundTagChange: (value: string) => void
  selectedPresetId: string
  onPresetChange: (value: string) => void
  canBind: boolean
  busy: boolean
  configured: boolean
  onBind: () => void
}

/** TLS binding controls keep private certificate keys in the backend. */
export function NodeTLSBindings({ bindings, presets, inboundTag, onInboundTagChange, selectedPresetId, onPresetChange,
  canBind, busy, configured, onBind }: Props) {
  return <section className="space-y-3 border-t pt-3" aria-label="TLS-сертификаты из пресетов WayLane">
    <div className="flex flex-wrap items-center justify-between gap-2">
      <h3 className="text-sm font-semibold">TLS-сертификаты из пресетов WayLane</h3>
      <Link className="text-sm font-medium text-primary underline underline-offset-2" to="/tls-presets?tab=bindings">Управление TLS-привязками</Link>
    </div>
    <p className="text-xs text-muted-foreground">Привязка сертификата к TLS inbound по tag. Приватные ключи остаются в Backend.</p>
    {bindings.length === 0 && <p className="text-xs text-muted-foreground">Привязок TLS пока нет.</p>}
    {bindings.map(binding => <p key={binding.inbound_tag} className="break-all rounded-md border p-2 text-xs text-muted-foreground">
      {binding.inbound_tag} · {binding.preset_name} · revision {binding.revision} · SHA-256 {binding.fingerprint_sha256.slice(0, 16)}…
    </p>)}
    {canBind && <div className="space-y-2">
      <Input aria-label="Agent TLS inbound tag" placeholder="Tag TLS inbound" value={inboundTag} maxLength={256}
        onChange={event => onInboundTagChange(event.target.value)} />
      <select aria-label="TLS preset" className="border-input bg-background w-full rounded-md border p-2 text-sm"
        value={selectedPresetId} onChange={event => onPresetChange(event.target.value)}>
        <option value="">Выберите существующий TLS-пресет</option>
        {presets.map(preset => <option key={preset.id} value={preset.id}>{preset.name} · revision {preset.revision}</option>)}
      </select>
      <Button size="sm" variant="outline" disabled={busy || !configured || !selectedPresetId || !inboundTag.trim()}
        onClick={onBind}>Привязать сертификат</Button>
    </div>}
  </section>
}
