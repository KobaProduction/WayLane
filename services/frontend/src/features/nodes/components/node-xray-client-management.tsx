import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'
import type { XrayInboundInfo, XrayStatus } from './node-xray-types'

type Props = {
  inbounds: XrayInboundInfo[]
  managedInboundTags: string[]
  runtime: XrayStatus | undefined
  inboundTag: string
  onInboundTagChange: (tag: string) => void
  usersJson: string
  onUsersJsonChange: (value: string) => void
  busy: boolean
  onManageInbound: (tag: string, enabled: boolean) => void
  onSyncUsers: () => void
}

export function NodeXrayClientManagement({ inbounds, managedInboundTags, runtime, inboundTag, onInboundTagChange,
  usersJson, onUsersJsonChange, busy, onManageInbound, onSyncUsers }: Props) {
  const isManaged = managedInboundTags.includes(inboundTag.trim())
  return <section className="space-y-3" aria-label="Xray clients and subscriptions">
    <div className="space-y-1">
      <h3 className="text-sm font-semibold">Пользователи Xray и подписки</h3>
      <p className="text-xs text-muted-foreground">WayLane может синхронизировать клиентов VLESS, VMess и Trojan по правам коммерческих подписок. SOCKS5 остаётся ручным.</p>
    </div>
    {inbounds.length === 0 && <p className="text-xs text-muted-foreground">Список входящих соединений пока пуст или не загружен.</p>}
    <div className="space-y-2">
      {inbounds.map(inbound => {
        const managed = managedInboundTags.includes(inbound.tag)
        const supported = ['vless', 'vmess', 'trojan'].includes(inbound.protocol)
        return <div key={inbound.tag} className="flex min-w-0 flex-wrap items-center justify-between gap-2 border-b py-2 text-xs">
          <div className="min-w-0 space-y-1">
            <p className="break-all font-medium">{inbound.tag} · {inbound.protocol} · {inbound.port}</p>
            <p className="text-muted-foreground">{managed ? 'Управляется подписками; фактическое число клиентов определяется Node' : `${inbound.user_count} ручных клиентов`}</p>
            {!managed && inbound.identities.length > 0 && <details>
              <summary className="cursor-pointer text-muted-foreground">Идентификаторы ручных клиентов ({inbound.identities.length})</summary>
              <p className="mt-1 break-all font-mono text-muted-foreground">{inbound.identities.join(', ')}</p>
            </details>}
          </div>
          {supported && <Button type="button" size="sm" variant="outline" disabled={busy || runtime?.runtime_status !== 'running'}
            onClick={() => onManageInbound(inbound.tag, !managed)}>{managed ? 'Отключить управление' : 'Управлять подписками'}</Button>}
        </div>
      })}
    </div>
    <details className="rounded-md border p-3">
      <summary className="cursor-pointer text-sm font-medium">Ручная синхронизация пользователей</summary>
      <div className="mt-3 space-y-2">
        <p className="text-xs text-muted-foreground">Заменяет список клиентов одного inbound. Для inbound под управлением подписок запрещено; пустой список требует подтверждения.</p>
        <Input aria-label="Xray inbound tag" value={inboundTag} maxLength={128} placeholder="Tag inbound"
          onChange={event => onInboundTagChange(event.target.value)} />
        <Textarea aria-label="Xray inbound users JSON" value={usersJson} rows={5} className="font-mono text-xs"
          placeholder={'[{"user":"qa","pass":"strong-password"}]'} onChange={event => onUsersJsonChange(event.target.value)} />
        <Button type="button" size="sm" variant="outline" disabled={busy || !usersJson.trim() || !inboundTag.trim() || !runtime?.configured || isManaged}
          onClick={onSyncUsers}>Синхронизировать пользователей</Button>
      </div>
    </details>
  </section>
}
