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

type AgentTLSBinding = {
  inbound_tag: string
  preset_id: string
  preset_name: string
  revision: number
  fingerprint_sha256: string
  not_after: string
}

type TLSPresetSummary = {
  id: string
  name: string
  revision: number
  fingerprint_sha256: string
}

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

type NodeRuntimeMetrics = {
  scope: 'agent-runtime'
  hostname: string
  os: string
  arch: string
  cpu_logical_count: number
  memory_total_bytes: number
  memory_available_bytes: number
  disk_total_bytes: number
  disk_free_bytes: number
  load_1: number
  uptime_seconds: number
}

type Registration = {
  node_uuid: string
  connection_mode: 'classic' | 'discovery'
  display_name: string
  status: 'pending' | 'approved' | 'revoked'
  node_version: string
  observed_ip: string | null
  reported_ipv4: string | null
  reported_ipv6: string | null
  capabilities: string[]
  last_seen_at: string | null
  machine_metrics: NodeRuntimeMetrics | null
}

/** Modern WayLane Node registry; legacy PasarGuard node cards remain separate. */
export default function NodeDiscoveryPanel() {
  const { admin } = useAdmin()
  const canRead = hasPermission(admin, 'nodes', 'read')
  const canCreate = hasPermission(admin, 'nodes', 'create')
  const canApprove = hasPermission(admin, 'nodes', 'update')
  const [entries, setEntries] = useState<Registration[]>([])
  const [invitations, setInvitations] = useState<Invitation[]>([])
  const [label, setLabel] = useState('')
  const [directCredentials, setDirectCredentials] = useState<{ node_uuid: string; node_token: string } | null>(null)
  const [registrationMode, setRegistrationMode] = useState<'classic' | 'discovery'>('classic')
  const [autoApprove, setAutoApprove] = useState(true)
  const [token, setToken] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const canManageXray = hasPermission(admin, 'nodes', 'update_core')
  const canBindTLS = hasPermission(admin, 'cores', 'update')
  const [xrayTarget, setXrayTarget] = useState<string | null>(null)
  const [xrayStates, setXrayStates] = useState<Record<string, XrayStatus>>({})
  const [xrayVersion, setXrayVersion] = useState('26.10.21')
  const [xrayRegion, setXrayRegion] = useState('')
  const [xrayConfig, setXrayConfig] = useState('')
  const [xrayBusy, setXrayBusy] = useState(false)
  const [xrayInboundTag, setXrayInboundTag] = useState('waylane-socks5-qa')
  const [xrayInboundStates, setXrayInboundStates] = useState<Record<string, XrayInboundInfo[]>>({})
  const [xrayUsersJSON, setXrayUsersJSON] = useState('')
  const [editorCoreId, setEditorCoreId] = useState('')
  const [tlsPresetId, setTlsPresetId] = useState('')
  const [tlsInboundTag, setTlsInboundTag] = useState('')
  const [agentTLSBindings, setAgentTLSBindings] = useState<Record<string, AgentTLSBinding[]>>({})
  const [tlsPresetChoices, setTlsPresetChoices] = useState<TLSPresetSummary[]>([])




  const refresh = async () => {
    try {
      const [result, invites] = await Promise.all([
        $fetch<{ nodes: Registration[] }>('/api/node-registry/nodes'),
        $fetch<{ invitations: Invitation[] }>('/api/node-registry/discovery-tokens'),
      ])
      setEntries(result.nodes)
      setInvitations(invites.invitations)
      setError('')
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Не удалось загрузить ноды')
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
      const response = await $fetch<{ discovery_token: string }>('/api/node-registry/discovery-tokens', {
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
    if (!canCreate) return
    setLoading(true)
    setDirectCredentials(null)
    try {
      const reply = await $fetch<{ node_uuid: string; node_token: string }>('/api/node-registry/classic', {
        method: 'POST',
      })
      setDirectCredentials(reply)
      await refresh()
      toast.success('Classic нода создана; сохраните её токен')
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Не удалось создать Classic-ноду')
    } finally { setLoading(false) }
  }

  const revokeInvitation = async (id: number) => {
    if (!canApprove) return
    setLoading(true)
    try {
      await $fetch('/api/node-registry/discovery-tokens/' + id + '/revoke', { method: 'POST' })
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
      await $fetch('/api/node-registry/nodes/' + encodeURIComponent(uuid) + '/' + operation, { method: 'POST' })
      await refresh()
      toast.success(operation === 'approve' ? 'Нода подтверждена' : 'Нода отозвана')
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Не удалось изменить статус ноды')
    } finally { setLoading(false) }
  }


  const readXray = async (uuid: string) => {
    try {
      const state = await $fetch<XrayStatus>('/api/node-registry/nodes/' + encodeURIComponent(uuid) + '/xray')
      setXrayStates(prev => ({ ...prev, [uuid]: state }))
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Не удалось получить статус Xray')
    }
  }

  const readXrayInbounds = async (uuid: string) => {
    try {
      const response = await $fetch<{ inbounds: XrayInboundInfo[] }>(
        '/api/node-registry/nodes/' + encodeURIComponent(uuid) + '/xray/inbounds')
      setXrayInboundStates(previous => ({ ...previous, [uuid]: response.inbounds }))
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Не удалось прочитать Xray inbounds')
    }
  }

  const refreshTLSBindings = async (uuid: string) => {
    try {
      const response = await $fetch<{ bindings: AgentTLSBinding[] }>(
        '/api/node-registry/nodes/' + encodeURIComponent(uuid) + '/xray/tls')
      setAgentTLSBindings(previous => ({ ...previous, [uuid]: response.bindings }))
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Не удалось получить TLS-привязки')
    }
  }

  const synchronizeEditorRouting = async (uuid: string) => {
    const generation = xrayStates[uuid]?.generation
    const coreId = Number(editorCoreId)
    if (!generation || !Number.isSafeInteger(coreId) || coreId < 1) {
      toast.error('Укажите существующий Core ID и обновите состояние Agent')
      return
    }
    setXrayBusy(true)
    try {
      await $fetch('/api/node-registry/nodes/' + encodeURIComponent(uuid) +
        '/xray/routing/from-core', {
        method: 'PUT', body: { core_id: coreId, expected_generation: generation },
      })
      toast.success('Маршрутизация сохранённого Core Editor поставлена в очередь Xray')
      await readXray(uuid)
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Маршрутизация не синхронизирована')
    } finally { setXrayBusy(false) }
  }

  const bindExistingTLS = async (uuid: string) => {
    const generation = xrayStates[uuid]?.generation
    if (!generation || !tlsPresetId || !tlsInboundTag.trim()) {
      toast.error('Выберите TLS-пресет и inbound, обновите статус Xray')
      return
    }
    setXrayBusy(true)
    try {
      await $fetch('/api/node-registry/nodes/' + encodeURIComponent(uuid) +
        '/xray/tls/' + encodeURIComponent(tlsInboundTag.trim()), {
        method: 'PUT', body: { preset_id: tlsPresetId, expected_generation: generation },
      })
      toast.success('TLS-пресет привязан; новая конфигурация отправлена ноде')
      await readXray(uuid)
      await refreshTLSBindings(uuid)
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'TLS-привязка не выполнена')
    } finally { setXrayBusy(false) }
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

  const changeXray = async (uuid: string, action: 'deploy' | 'update' | 'restart' | 'stop' | 'reconfigure') => {
    if (!canManageXray) return
    setXrayBusy(true)
    try {
      const payload: { action: string; image_version: string; region: string | null; config?: Record<string, unknown>; expected_generation?: number } = {
        action, image_version: xrayVersion.trim(), region: xrayRegion.trim() || null,
      }
      if (action === 'reconfigure') {
        const generation = xrayStates[uuid]?.generation
        if (!generation) throw new Error('Сначала обновите статус Xray')
        payload.expected_generation = generation
      }
      if (action === 'deploy' || action === 'update' || action === 'reconfigure') {
        if (xrayConfig.trim()) {
          const parsed: unknown = JSON.parse(xrayConfig)
          if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) throw new Error('Требуется объект JSON')
          payload.config = parsed as Record<string, unknown>
        }
      }
      await $fetch('/api/node-registry/nodes/' + encodeURIComponent(uuid) + '/xray', {
        method: 'PUT', body: payload,
      })
      toast.success('Операция Xray поставлена в очередь ноды')
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
      await $fetch('/api/node-registry/nodes/' + encodeURIComponent(uuid) +
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
        <CardTitle>Управление нодами</CardTitle>
        <CardDescription>
          Нода устанавливается на сервер и включает bootstrap, управляющий процесс и отдельные VPN-ядра.
          Выберите способ регистрации: управляемая панелью нода или самостоятельное подключение через Discovery.
          Уже работающие legacy-ноды и клиентский трафик не переключаются автоматически.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="flex flex-col gap-3 rounded-md border p-3">
          <p className="text-sm font-medium">Способ подключения новой ноды</p>
          <div className="grid grid-cols-1 gap-2 sm:grid-cols-2" role="group" aria-label="Способ регистрации ноды">
            <Button type="button" variant={registrationMode === 'classic' ? 'default' : 'outline'}
              onClick={() => setRegistrationMode('classic')}>
              Classic
            </Button>
            <Button type="button" variant={registrationMode === 'discovery' ? 'default' : 'outline'}
              onClick={() => setRegistrationMode('discovery')}>
              Discovery
            </Button>
          </div>
          {registrationMode === 'classic' ? (
            <div className="space-y-2">
              <p className="text-muted-foreground text-xs">
                Панель создаёт отдельную ноду и выдаёт одноразовый NODE_TOKEN. После подключения нода автоматически передаст hostname сервера — отдельное поле имени не требуется.
                Используйте docker-compose.node.classic.coolify.yaml. Внутри ноды работают bootstrap и управляющий процесс.
              </p>
              {canCreate && (
                <Button type="button" disabled={loading} onClick={() => void createDirect()}>
                  Создать Classic-ноду
                </Button>
              )}
              {directCredentials && (
                <div className="space-y-2 rounded-md border p-3">
                  <p className="text-sm">Токен отображается один раз. Сохраните его в защищённой переменной Coolify.</p>
                  <p className="break-all font-mono text-xs">UUID: {directCredentials.node_uuid}</p>
                  <Input aria-label="Токен Classic-ноды" type="password" readOnly value={directCredentials.node_token}
                    className="font-mono text-xs" />
                  <div className="flex flex-wrap gap-2">
                    <Button type="button" variant="outline" size="sm"
                      onClick={() => void navigator.clipboard.writeText('NODE_TOKEN=' + directCredentials.node_token)}>
                      Копировать NODE_TOKEN
                    </Button>
                    <Button type="button" variant="ghost" size="sm" onClick={() => setDirectCredentials(null)}>Скрыть</Button>
                  </div>
                </div>
              )}
            </div>
          ) : (
            <div className="space-y-2">
              <p className="text-muted-foreground text-xs">
                Нода сама регистрируется по Discovery-токену. Используйте docker-compose.node.discovery.coolify.yaml.
                Новые ноды можно подтверждать автоматически или вручную.
              </p>
              {canCreate && (
                <div className="flex flex-col gap-2 sm:flex-row">
                  <Input className="min-w-0 flex-1" aria-label="Название Discovery-группы"
                    placeholder="Группа или приглашение" value={label}
                    onChange={e => setLabel(e.target.value)} maxLength={120} />
                  <Button type="button" onClick={() => void invite()} disabled={loading || !label.trim()} className="w-full sm:w-auto">
                    Создать Discovery-токен
                  </Button>
                </div>
              )}
              {canCreate && (
                <label className="flex items-start gap-2 text-sm">
                  <Checkbox checked={autoApprove} onCheckedChange={checked => setAutoApprove(checked === true)} />
                  <span>Подтверждать новые ноды автоматически (это не разрешает управление VPN-ядрами)</span>
                </label>
              )}
              {token && (
                <div className="min-w-0 space-y-2 rounded-md border p-3">
                  <p className="text-sm">Токен отображается один раз. В панели хранится только его хеш.</p>
                  <div className="flex min-w-0 flex-col gap-2 sm:flex-row sm:items-center">
                    <Input aria-label="Discovery-токен" className="min-w-0 flex-1 font-mono text-xs"
                      value={token} readOnly type="password" />
                    <Button variant="outline" type="button"
                      onClick={() => void navigator.clipboard.writeText('DISCOVERY_TOKEN=' + token)}>
                      Копировать DISCOVERY_TOKEN
                    </Button>
                    <Button variant="ghost" type="button" onClick={() => setToken('')}>Скрыть</Button>
                  </div>
                </div>
              )}
            </div>
          )}
          <p className="text-muted-foreground text-xs">
            Единственный адрес CONTROL_PANEL_URL используется для регистрации по HTTPS
            и постоянного защищённого WebSocket (WSS) с бинарными сообщениями Protobuf.
            Панель передаёт команды, нода возвращает состояние и телеметрию по тому же соединению.
            Открывать входящий порт управления на ноде не требуется.
          </p>
        </div>
        {registrationMode === 'discovery' && invitations.length > 0 && (
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
          <span className="text-sm font-medium">Подключённые и обнаруженные ноды ({entries.length})</span>
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
                  <Badge variant="outline">{node.connection_mode === 'classic' ? 'Classic' : 'Discovery'}</Badge>
                </div>
                <p className="text-muted-foreground break-all font-mono text-xs">{node.node_uuid}</p>
                <p className="text-muted-foreground break-words text-xs">
                  Управляющий процесс {node.node_version} · {node.last_seen_at ? 'Был на связи' : 'Ожидает первого heartbeat'} · адрес соединения {node.observed_ip ?? 'неизвестен'} · внешний IP {node.reported_ipv4 ?? node.reported_ipv6 ?? 'определяется'}
                </p>
                {node.machine_metrics && (
                  <p className="text-muted-foreground break-words text-xs">
                    Окружение ноды ({node.machine_metrics.scope}): {node.machine_metrics.os}/{node.machine_metrics.arch}
                    {' · '}CPU {node.machine_metrics.cpu_logical_count}
                    {' · '}RAM {Math.round(node.machine_metrics.memory_total_bytes / 1024 / 1024)} MiB
                    {' · '}load {node.machine_metrics.load_1.toFixed(2)}
                    {' · '}контейнер {node.machine_metrics.hostname}
                  </p>
                )}
              </div>
              {canManageXray && node.status === 'approved' && (
                <div className="min-w-0 w-full space-y-2 border-t pt-2 sm:basis-full">
                  <div className="flex flex-wrap items-center gap-2">
                    <Button size="sm" variant="outline" onClick={() => {
                      setXrayTarget(xrayTarget === node.node_uuid ? null : node.node_uuid)
                      void readXray(node.node_uuid)
                      void readXrayInbounds(node.node_uuid)
                      void refreshTLSBindings(node.node_uuid)
                      void $fetch<TLSPresetSummary[]>('/api/tls-presets')
                        .then(setTlsPresetChoices).catch(() => setTlsPresetChoices([]))
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
                          onClick={() => void changeXray(node.node_uuid, 'update')}>Обновить образ Xray (с перезапуском)</Button>
                        <Button variant="outline" size="sm" disabled={xrayBusy || !xrayConfig.trim()}
                          onClick={() => void changeXray(node.node_uuid, 'reconfigure')}>Применить конфигурацию без рестарта Xray</Button>
                        <Button variant="outline" size="sm" disabled={xrayBusy}
                          onClick={() => void changeXray(node.node_uuid, 'restart')}>Перезапустить</Button>
                        <Button variant="destructive" size="sm" disabled={xrayBusy}
                          onClick={() => void changeXray(node.node_uuid, 'stop')}>Остановить</Button>
                        <Button variant="outline" size="sm" disabled={xrayBusy}
                          onClick={() => void readXray(node.node_uuid)}>Статус</Button>
                      </div>
                      <div className="space-y-2 border-t pt-3">
                        <p className="text-sm font-medium">Маршрутизация из Core Editor</p>
                        <p className="text-muted-foreground text-xs">
                          Использует сохранённые правила, balancers и параметры маршрутизации Xray Core Editor.
                          Остальные разделы текущего Xray не изменяются.
                        </p>
                        <div className="flex flex-wrap gap-2">
                          <Input aria-label="Saved Xray core ID" className="max-w-52" type="number"
                            min="1" placeholder="ID сохранённого Xray Core"
                            value={editorCoreId} onChange={e => setEditorCoreId(e.target.value)} />
                          <Button size="sm" variant="outline" disabled={xrayBusy || !xrayStates[node.node_uuid]?.configured}
                            onClick={() => void synchronizeEditorRouting(node.node_uuid)}>
                            Применить правила редактора
                          </Button>
                        </div>
                      </div>
                      <div className="space-y-2 border-t pt-3">
                        <p className="text-sm font-medium">TLS-сертификаты из пресетов WayLane</p>
                        <p className="text-muted-foreground text-xs">
                          Привязка по tag TLS inbound. Приватный ключ не отображается; ротация пресета автоматически
                          поставит обновление Xray в очередь.
                        </p>
                        {agentTLSBindings[node.node_uuid]?.map(binding => (
                          <p key={binding.inbound_tag} className="text-muted-foreground text-xs break-all">
                            {binding.inbound_tag} · {binding.preset_name} · ревизия {binding.revision} · SHA-256 {binding.fingerprint_sha256.slice(0, 16)}…
                          </p>
                        ))}
                        <Input aria-label="Agent TLS inbound tag" placeholder="Tag TLS inbound"
                          value={tlsInboundTag} onChange={e => setTlsInboundTag(e.target.value)} maxLength={256} />
                        <select aria-label="TLS preset" className="border-input bg-background w-full rounded-md border p-2 text-sm"
                          value={tlsPresetId} onChange={e => setTlsPresetId(e.target.value)}>
                          <option value="">Выберите существующий TLS-пресет</option>
                          {tlsPresetChoices.map(preset => (
                            <option key={preset.id} value={preset.id}>
                              {preset.name} · revision {preset.revision}
                            </option>
                          ))}
                        </select>
                        {canBindTLS && (
                          <Button size="sm" variant="outline"
                            disabled={xrayBusy || !xrayStates[node.node_uuid]?.configured}
                            onClick={() => void bindExistingTLS(node.node_uuid)}>Привязать сертификат</Button>
                        )}
                      </div>
                      <div className="space-y-2 border-t pt-3">
                        <p className="text-sm font-medium">Пользователи inbound (синхронизация)</p>
                        <p className="text-muted-foreground text-xs">Заменяет аккаунты выбранного inbound, остальные inbounds и outbounds сохраняет. Пароли передаются только авторизованной ноде через API. Пустой массив удаляет всех пользователей выбранного inbound.</p>
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
