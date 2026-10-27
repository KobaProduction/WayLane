import { useEffect, useState } from 'react'
import { $fetch } from '@/service/http'
import { useAdmin } from '@/hooks/use-admin'
import { hasPermission } from '@/utils/rbac'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'
import { Badge } from '@/components/ui/badge'
import { Checkbox } from '@/components/ui/checkbox'

type XrayInboundInfo = {
  tag: string
  protocol: string
  port: number
  user_count: number
  identities: string[]
}

type XrayStatus = {
  configured: boolean
  generation?: number
  image_version?: string
  action?: string
  region?: string | null
  runtime_status?: string
  applied_generation?: number
  last_error?: string | null
}

type Invitation = {
  id: number
  label: string
  revoked: '0' | '1'
  created_at: string
  expires_at: string | null
  auto_approve: '0' | '1'
}

type Registration = {
  node_uuid: string
  enrollment_mode: 'direct' | 'discovery'
  display_name: string
  status: 'pending' | 'approved' | 'revoked'
  agent_version: string
  observed_ip: string | null
  reported_ipv4: string | null
  reported_ipv6: string | null
  capabilities: string[]
  last_seen_at: string | null
}

/** Optional Agent registry, separate from the existing legacy node cards. */
export default function NodeDiscoveryPanel() {
  const { admin } = useAdmin()
  const canRead = hasPermission(admin, 'nodes', 'read')
  const canCreate = hasPermission(admin, 'nodes', 'create')
  const canApprove = hasPermission(admin, 'nodes', 'update')
  const [entries, setEntries] = useState<Registration[]>([])
  const [invitations, setInvitations] = useState<Invitation[]>([])
  const [label, setLabel] = useState('')
  const [directName, setDirectName] = useState('')
  const [directCredentials, setDirectCredentials] = useState<{ node_uuid: string; agent_token: string } | null>(null)
  const [autoApprove, setAutoApprove] = useState(true)
  const [token, setToken] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const canManageXray = hasPermission(admin, 'nodes', 'update_core')
  const [xrayTarget, setXrayTarget] = useState<string | null>(null)
  const [xrayStates, setXrayStates] = useState<Record<string, XrayStatus>>({})
  const [xrayVersion, setXrayVersion] = useState('26.10.21')
  const [xrayRegion, setXrayRegion] = useState('')
  const [xrayConfig, setXrayConfig] = useState('')
  const [xrayBusy, setXrayBusy] = useState(false)
  const [xrayInboundTag, setXrayInboundTag] = useState('waylane-socks5-qa')
  const [xrayInboundStates, setXrayInboundStates] = useState<Record<string, XrayInboundInfo[]>>({})
  const [xrayUsersJSON, setXrayUsersJSON] = useState('')



  const refresh = async () => {
    try {
      const [result, invites] = await Promise.all([
        $fetch<{ registrations: Registration[] }>('/api/node-discovery/registrations'),
        $fetch<{ invitations: Invitation[] }>('/api/node-discovery/invitations'),
      ])
      setEntries(result.registrations)
      setInvitations(invites.invitations)
      setError('')
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load discovered agents')
    }
  }

  useEffect(() => {
    if (!canRead) return
    void refresh()
    // Discovery is a live node registry. Keep it fresh while this view exists.
    const timer = window.setInterval(() => {
      if (document.visibilityState === 'visible') void refresh()
    }, 30000)
    return () => window.clearInterval(timer)
  }, [canRead])

  const invite = async () => {
    if (!label.trim() || !canCreate) return
    setLoading(true)
    setToken('')
    try {
      const response = await $fetch<{ discovery_token: string }>('/api/node-discovery/invitations', {
        method: 'POST', body: { label: label.trim(), auto_approve: autoApprove },
      })
      setToken(response.discovery_token)
      setLabel('')
      await refresh()
      toast.success('Discovery token created')
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Invitation could not be created')
    } finally { setLoading(false) }
  }

  const createDirect = async () => {
    if (!canCreate || !directName.trim()) return
    setLoading(true)
    setDirectCredentials(null)
    try {
      const reply = await $fetch<{ node_uuid: string; agent_token: string }>('/api/node-discovery/registrations/direct', {
        method: 'POST', body: { display_name: directName.trim() },
      })
      setDirectCredentials(reply)
      setDirectName('')
      await refresh()
      toast.success('Прямой Agent-токен создан')
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Не удалось создать прямой Agent-токен')
    } finally { setLoading(false) }
  }

  const revokeInvitation = async (id: number) => {
    if (!canApprove) return
    setLoading(true)
    try {
      await $fetch('/api/node-discovery/invitations/' + id + '/revoke', { method: 'POST' })
      await refresh()
      toast.success('Discovery invitation revoked')
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Failed to revoke invitation')
    } finally { setLoading(false) }
  }

  const decide = async (uuid: string, operation: 'approve' | 'revoke') => {
    if (!canApprove) return
    setLoading(true)
    try {
      await $fetch('/api/node-discovery/registrations/' + encodeURIComponent(uuid) + '/' + operation, { method: 'POST' })
      await refresh()
      toast.success(operation === 'approve' ? 'Agent approved' : 'Agent revoked')
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Failed to update agent')
    } finally { setLoading(false) }
  }


  const readXray = async (uuid: string) => {
    try {
      const state = await $fetch<XrayStatus>('/api/node-discovery/registrations/' + encodeURIComponent(uuid) + '/xray')
      setXrayStates(prev => ({ ...prev, [uuid]: state }))
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Не удалось получить статус Xray')
    }
  }

  const readXrayInbounds = async (uuid: string) => {
    try {
      const response = await $fetch<{ inbounds: XrayInboundInfo[] }>(
        '/api/node-discovery/registrations/' + encodeURIComponent(uuid) + '/xray/inbounds')
      setXrayInboundStates(previous => ({ ...previous, [uuid]: response.inbounds }))
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Не удалось прочитать Xray inbounds')
    }
  }

  const demoXray = () => {
    // Generated only when requested. Never commit a reusable proxy password.
    const random = crypto.getRandomValues(new Uint8Array(24))
    const password = Array.from(random, value => value.toString(16).padStart(2, '0')).join('')
    setXrayConfig(JSON.stringify({
      log: { loglevel: 'warning' },
      inbounds: [{ tag: 'waylane-socks-qa', listen: '0.0.0.0', port: 18443,
        protocol: 'socks', settings: { auth: 'password', accounts: [{ user: 'waylane-qa', pass: password }], udp: false } }],
      outbounds: [{ tag: 'direct', protocol: 'freedom' }],
    }, null, 2))
  }

  const changeXray = async (uuid: string, action: 'deploy' | 'update' | 'restart' | 'stop') => {
    if (!canManageXray) return
    setXrayBusy(true)
    try {
      const payload: { action: string; image_version: string; region: string | null; config?: Record<string, unknown> } = {
        action, image_version: xrayVersion.trim(), region: xrayRegion.trim() || null,
      }
      if (action === 'deploy' || action === 'update') {
        if (xrayConfig.trim()) {
          const parsed: unknown = JSON.parse(xrayConfig)
          if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) throw new Error('Требуется объект JSON')
          payload.config = parsed as Record<string, unknown>
        }
      }
      await $fetch('/api/node-discovery/registrations/' + encodeURIComponent(uuid) + '/xray', {
        method: 'PUT', body: payload,
      })
      toast.success('Операция поставлена в желаемое состояние Agent')
      await readXray(uuid)
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Не удалось отправить команду Xray')
    } finally { setXrayBusy(false) }
  }

  const synchronizeInboundUsers = async (uuid: string) => {
    if (!canManageXray) return
    setXrayBusy(true)
    try {
      const expectedGeneration = xrayStates[uuid]?.generation
      if (!expectedGeneration) throw new Error('Сначала обновите статус Xray')
      const users: unknown = JSON.parse(xrayUsersJSON)
      if (!Array.isArray(users) || users.some(user => !user || typeof user !== 'object' || Array.isArray(user))) {
        throw new Error('Нужен JSON-массив пользователей')
      }
      if (!xrayInboundTag.trim()) throw new Error('Укажите tag inbound')
      if (users.length === 0 && !window.confirm('Удалить всех пользователей из выбранного inbound?')) return
      await $fetch('/api/node-discovery/registrations/' + encodeURIComponent(uuid) +
        '/xray/inbounds/' + encodeURIComponent(xrayInboundTag.trim()) + '/users', {
        method: 'PUT', body: { expected_generation: expectedGeneration, users },
      })
      toast.success('Синхронизация пользователей Xray запланирована')
      await readXray(uuid)
      await readXrayInbounds(uuid)
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Ошибка синхронизации пользователей Xray')
    } finally { setXrayBusy(false) }
  }

  if (!canRead) return null

  return (
    <Card className="mb-5">
      <CardHeader>
        <CardTitle>WayLane Agent — подключения</CardTitle>
        <CardDescription>
          Подключение через Discovery-токен или индивидуальный Agent-токен. Адреса обновляются через heartbeat.
          Управление VPN-ядрами пока не включено, существующие хосты и подписки не изменяются.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {canCreate && (
          <div className="flex flex-col gap-2 sm:flex-row">
            <Input
              className="min-w-0 flex-1"
              aria-label="Discovery invitation name"
              placeholder="Название токена для группы нод"
              value={label}
              onChange={e => setLabel(e.target.value)}
              maxLength={120}
            />
            <Button type="button" onClick={() => void invite()} disabled={loading || !label.trim()} className="w-full sm:w-auto">
              Создать токен
            </Button>
          </div>
        )}
        {canCreate && (
          <label className="flex items-start gap-2 text-sm">
            <Checkbox checked={autoApprove} onCheckedChange={checked => setAutoApprove(checked === true)} />
            <span>Автоматически подключать новые агенты с этим токеном (без разрешения на управление VPN-ядрами)</span>
          </label>
        )}
        {token && (
          <div className="min-w-0 space-y-2 rounded-md border p-3">
            <p className="text-sm">Скопируйте токен сейчас. В панели хранится только его хеш; повторно показать значение нельзя.</p>
            <div className="flex min-w-0 flex-col gap-2 sm:flex-row sm:items-center">
              <Input aria-label="New discovery token" className="min-w-0 flex-1 font-mono text-xs" value={token} readOnly type="password" />
              <Button variant="outline" type="button" onClick={() => void navigator.clipboard.writeText(token)}>Копировать</Button>
              <Button variant="ghost" type="button" onClick={() => setToken('')}>Скрыть</Button>
            </div>
          </div>
        )}
        <div className="space-y-3 border-t pt-4">
          <p className="text-sm font-medium">Прямое подключение по индивидуальному Agent-токену</p>
          {canCreate && (
            <div className="flex flex-col gap-2 sm:flex-row">
              <Input
                aria-label="Direct agent name"
                placeholder="Название агента"
                value={directName}
                maxLength={128}
                onChange={e => setDirectName(e.target.value)}
                className="min-w-0 flex-1"
              />
              <Button type="button" onClick={() => void createDirect()} disabled={loading || !directName.trim()}>
                Создать Agent-токен
              </Button>
            </div>
          )}
          {directCredentials && (
            <div className="space-y-2 rounded-md border p-3">
              <p className="text-sm">Сохраните идентификатор и токен сейчас. Сам токен повторно не отображается.</p>
              <p className="break-all font-mono text-xs">UUID ноды: {directCredentials.node_uuid}</p>
              <Input aria-label="Direct agent token" type="password" readOnly value={directCredentials.agent_token} className="font-mono text-xs" />
              <div className="flex flex-wrap gap-2">
                <Button type="button" variant="outline" size="sm"
                  onClick={() => void navigator.clipboard.writeText(
                    'WAYLANE_AGENT_TOKEN=' + directCredentials.agent_token
                  )}>Копировать Agent-токен</Button>
                <Button type="button" variant="ghost" size="sm" onClick={() => setDirectCredentials(null)}>Скрыть</Button>
              </div>
            </div>
          )}
        </div>
        {invitations.length > 0 && (
          <div className="space-y-2">
            <p className="text-sm font-medium">Discovery-токены ({invitations.length})</p>
            {invitations.map(item => (
              <div key={item.id} className="flex min-w-0 flex-wrap items-center justify-between gap-2 rounded-md border p-3">
                <div className="min-w-0 space-y-1">
                  <span className="break-words text-sm">{item.label}</span>
                  <p className="text-muted-foreground text-xs">{item.revoked === '1' ? 'Отозван' : item.expires_at ? 'Ограничен по времени' : 'Постоянный'} · {item.auto_approve === '1' ? 'Автоподключение' : 'Ручное подтверждение'}</p>
                </div>
                {canApprove && item.revoked !== '1' && (
                  <Button type="button" size="sm" variant="outline" disabled={loading}
                    onClick={() => void revokeInvitation(item.id)}>Отозвать токен</Button>
                )}
              </div>
            ))}
          </div>
        )}
        <div className="flex items-center justify-between gap-2">
          <span className="text-sm font-medium">Подключённые и обнаруженные агенты ({entries.length})</span>
          <Button type="button" variant="outline" size="sm" onClick={() => void refresh()} disabled={loading}>Обновить</Button>
        </div>
        {error && <p role="alert" className="text-destructive text-sm">{error}</p>}
        {entries.length === 0 && !error && <p className="text-muted-foreground text-sm">Заявок пока нет.</p>}
        <div className="space-y-2">
          {entries.map(node => (
            <div key={node.node_uuid} className="flex min-w-0 flex-col justify-between gap-3 rounded-md border p-3 sm:flex-row sm:items-center">
              <div className="min-w-0 space-y-1">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="min-w-0 break-words text-sm font-medium">{node.display_name}</span>
                  <Badge variant="secondary">{node.status}</Badge>
                  <Badge variant="outline">{node.enrollment_mode === 'direct' ? 'Agent-токен' : 'Discovery'}</Badge>
                </div>
                <p className="text-muted-foreground break-all font-mono text-xs">{node.node_uuid}</p>
                <p className="text-muted-foreground break-words text-xs">
                  Agent {node.agent_version} · {node.last_seen_at ? 'Был на связи' : 'Ожидает первого heartbeat'} · адрес соединения {node.observed_ip ?? 'неизвестен'} · внешний IP {node.reported_ipv4 ?? node.reported_ipv6 ?? 'определяется'}
                </p>
              </div>
              {canManageXray && node.status === 'approved' && (
                <div className="min-w-0 w-full space-y-2 border-t pt-2 sm:basis-full">
                  <div className="flex flex-wrap items-center gap-2">
                    <Button size="sm" variant="outline" onClick={() => {
                      setXrayTarget(xrayTarget === node.node_uuid ? null : node.node_uuid)
                      void readXray(node.node_uuid)
                      void readXrayInbounds(node.node_uuid)
                    }}>Xray: управление</Button>
                    <span className="text-muted-foreground text-xs">
                      {xrayStates[node.node_uuid]?.configured
                        ? `Версия: ${xrayStates[node.node_uuid].image_version} · ${xrayStates[node.node_uuid].runtime_status} · поколение ${xrayStates[node.node_uuid].applied_generation}/${xrayStates[node.node_uuid].generation}`
                        : 'Xray не настроен'}
                    </span>
                  </div>
                  {xrayTarget === node.node_uuid && (
                    <div className="space-y-2">
                      <div className="grid gap-2 sm:grid-cols-2">
                        <Input aria-label="Xray image version" value={xrayVersion}
                          onChange={e => setXrayVersion(e.target.value)} placeholder="Версия Xray, например 26.10.21" />
                        <Input aria-label="Xray region label" value={xrayRegion}
                          onChange={e => setXrayRegion(e.target.value)} maxLength={64}
                          placeholder="Регион / метка маршрута" />
                      </div>
                      <p className="text-muted-foreground text-xs">Метка региона не перемещает сервер и не меняет IP. Маршрут и сервер назначения задаются через JSON-конфигурацию Xray.</p>
                      <Textarea aria-label="Xray configuration JSON" value={xrayConfig} rows={8}
                        className="font-mono text-xs" onChange={e => setXrayConfig(e.target.value)}
                        placeholder="JSON-конфигурация Xray: inbounds, outbounds, routing" />
                      <div className="flex flex-wrap gap-2">
                        <Button variant="outline" size="sm" onClick={demoXray}>Тестовый SOCKS5 с новым паролем</Button>
                        <Button size="sm" disabled={xrayBusy || !xrayConfig.trim()}
                          onClick={() => void changeXray(node.node_uuid, 'deploy')}>Развернуть</Button>
                        <Button variant="outline" size="sm" disabled={xrayBusy}
                          onClick={() => void changeXray(node.node_uuid, 'update')}>Скачать и обновить</Button>
                        <Button variant="outline" size="sm" disabled={xrayBusy}
                          onClick={() => void changeXray(node.node_uuid, 'restart')}>Перезапустить</Button>
                        <Button variant="destructive" size="sm" disabled={xrayBusy}
                          onClick={() => void changeXray(node.node_uuid, 'stop')}>Остановить</Button>
                        <Button variant="outline" size="sm" disabled={xrayBusy}
                          onClick={() => void readXray(node.node_uuid)}>Статус</Button>
                      </div>
                      <div className="space-y-2 border-t pt-3">
                        <p className="text-sm font-medium">Пользователи inbound (синхронизация)</p>
                        <p className="text-muted-foreground text-xs">Заменяет аккаунты выбранного inbound, остальные inbounds и outbounds сохраняет. Пароли передаются только авторизованному Agent через API. Пустой массив удаляет всех пользователей выбранного inbound.</p>
                        <div className="space-y-1 text-xs text-muted-foreground">
                          {(xrayInboundStates[node.node_uuid] || []).map(inbound => (
                            <p key={inbound.tag} className="break-all">
                              {inbound.tag} · {inbound.protocol} · {inbound.user_count} пользователей
                              {inbound.identities.length ? ': ' + inbound.identities.join(', ') : ''}
                            </p>
                          ))}
                        </div>
                        <Input aria-label="Xray inbound tag" placeholder="Tag inbound"
                          value={xrayInboundTag} onChange={e => setXrayInboundTag(e.target.value)} maxLength={128} />
                        <Textarea aria-label="Xray inbound users JSON" value={xrayUsersJSON}
                          rows={5} className="font-mono text-xs"
                          placeholder={'[{"user":"qa","pass":"strong-password"}]'}
                          onChange={e => setXrayUsersJSON(e.target.value)} />
                        <Button size="sm" variant="outline"
                          disabled={xrayBusy || !xrayUsersJSON.trim() || !xrayStates[node.node_uuid]?.configured}
                          onClick={() => void synchronizeInboundUsers(node.node_uuid)}>
                          Синхронизировать пользователей
                        </Button>
                      </div>
                      {xrayStates[node.node_uuid]?.last_error && (
                        <p role="alert" className="text-destructive text-xs">{xrayStates[node.node_uuid].last_error}</p>
                      )}
                    </div>
                  )}
                </div>
              )}
              {canApprove && node.status !== 'revoked' && (
                <div className="flex shrink-0 gap-2">
                  {node.status === 'pending' && <Button size="sm" disabled={loading} onClick={() => void decide(node.node_uuid, 'approve')}>Подтвердить</Button>}
                  <Button variant="outline" size="sm" disabled={loading} onClick={() => void decide(node.node_uuid, 'revoke')}>Отозвать</Button>
                </div>
              )}
            </div>
          ))}
        </div>
      </CardContent>
    </Card>
  )
}
