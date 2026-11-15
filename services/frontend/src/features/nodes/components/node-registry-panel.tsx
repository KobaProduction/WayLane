import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router'
import { $fetch } from '@/service/http'
import { useAdmin } from '@/hooks/use-admin'
import { hasPermission } from '@/utils/rbac'
import { toast } from 'sonner'
import { OFFICIAL_NODE_REPOSITORY, approvedNodeReleaseImage, withNodeReleaseRepository } from './node-release-image'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'
import { Badge } from '@/components/ui/badge'
import { AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle, AlertDialogTrigger } from '@/components/ui/alert-dialog'

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

type XrayUsageRow = {
  subscription_uuid: string
  total_uplink: string
  total_downlink: string
  epoch_started_at: string
  observed_at: string
}
type XrayUsageState = {
  billing_authoritative: false
  billing_enabled: boolean
  source: string
  samples: XrayUsageRow[]
}

type XrayStatus = {
  configured: boolean
  generation?: number
  image_version?: string
  action?: string
  region?: string | null
  runtime_status?: string
  applied_generation?: number
  source_core_id?: number | null
  last_error?: string | null
}

type EditorCoreOption = { id: number; name: string; type: string | null }

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

type NodeReleaseOperation = {
  operation_id: string | null
  generation: number
  action: 'update' | 'reconnect' | null
  image: string | null
  previous_image: string | null
  status: 'idle' | 'pending' | 'queued' | 'running' | 'succeeded' | 'failed' | 'rolled_back'
  phase: string
  progress: number
  error_code: string | null
  updated_at?: string | null
}

type NodeRegistryProfile = {
  repository: string
  username: string
  has_credentials: boolean
  credential_scope?: 'node' | 'shared' | 'none'
  official: boolean
  is_default: boolean
}

type RegistryDraft = { repository: string; username: string; password: string }

type Registration = {
  node_uuid: string
  connection_mode: 'classic' | 'discovery'
  display_name: string
  status: 'pending' | 'approved' | 'revoked'
  node_version: string
  node_image: string | null
  observed_ip: string | null
  reported_ipv4: string | null
  reported_ipv6: string | null
  capabilities: string[]
  last_seen_at: string | null
  machine_metrics: NodeRuntimeMetrics | null
}

/** Autonomous WayLane Node registration and supported core controls. */
export default function NodeDiscoveryPanel() {
  const { admin } = useAdmin()
  const { t } = useTranslation()
  const canRead = hasPermission(admin, 'nodes', 'read')
  const canCreate = hasPermission(admin, 'nodes', 'create')
  const canApprove = hasPermission(admin, 'nodes', 'update')
  const [entries, setEntries] = useState<Registration[]>([])
  const [nameEditors, setNameEditors] = useState<Record<string, string>>({})
  const [releaseTarget, setReleaseTarget] = useState<string | null>(null)
  const [releaseTargets, setReleaseTargets] = useState<Record<string, string>>({})
  const [registryProfiles, setRegistryProfiles] = useState<Record<string, NodeRegistryProfile[]>>({})
  const [registryDraft, setRegistryDraft] = useState<RegistryDraft>({ repository: '', username: '', password: '' })
  const [registryBusy, setRegistryBusy] = useState(false)
  const [releaseStates, setReleaseStates] = useState<Record<string, NodeReleaseOperation>>({})
  const [releaseBusy, setReleaseBusy] = useState(false)
  const [renameBusy, setRenameBusy] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const canManageXray = hasPermission(admin, 'nodes', 'update_core')
  const canBindTLS = hasPermission(admin, 'cores', 'update')
  const [xrayTarget, setXrayTarget] = useState<string | null>(null)
  const [xrayStates, setXrayStates] = useState<Record<string, XrayStatus>>({})
  const [xrayUsageStates, setXrayUsageStates] = useState<Record<string, XrayUsageState>>({})
  const [xrayVersion, setXrayVersion] = useState('26.10.21')
  const [xrayRegion, setXrayRegion] = useState('')
  const [xrayConfig, setXrayConfig] = useState('')
  const [xrayBusy, setXrayBusy] = useState(false)
  const [xrayInboundTag, setXrayInboundTag] = useState('waylane-socks5-qa')
  const [xrayInboundStates, setXrayInboundStates] = useState<Record<string, XrayInboundInfo[]>>({})
  const [managedInboundStates, setManagedInboundStates] = useState<Record<string, string[]>>({})
  const [xrayUsersJSON, setXrayUsersJSON] = useState('')
  const [editorCoreIds, setEditorCoreIds] = useState<Record<string, string>>({})
  const [editorCores, setEditorCores] = useState<EditorCoreOption[]>([])
  const [tlsPresetId, setTlsPresetId] = useState('')
  const [tlsInboundTag, setTlsInboundTag] = useState('')
  const [agentTLSBindings, setAgentTLSBindings] = useState<Record<string, AgentTLSBinding[]>>({})
  const [tlsPresetChoices, setTlsPresetChoices] = useState<TLSPresetSummary[]>([])




  const loadNodeLifecycle = async (uuid: string) => {
    try {
      const state = await $fetch<NodeReleaseOperation>(
        '/api/node-registry/nodes/' + encodeURIComponent(uuid) + '/operation')
      setReleaseStates(previous => ({ ...previous, [uuid]: state }))
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Не удалось получить операцию Node')
    }
  }

  const requestNodeLifecycle = async (uuid: string, action: 'update' | 'reconnect') => {
    if (!canApprove || releaseBusy) return
    const image = releaseTargets[uuid]?.trim() ?? ''
    if (action === 'update' && !approvedNodeReleaseImage(image,
      (registryProfiles[uuid] ?? [{ repository: OFFICIAL_NODE_REPOSITORY }]).map(profile => profile.repository))) {
      toast.error('Укажите точный digest образа из разрешённого GHCR или собственного Registry')
      return
    }
    const message = action === 'update'
      ? 'Обновить только внутреннюю Node? Управляющий Agent останется установленным, Node кратковременно прервёт WSS; при неудаче Agent выполнит откат.'
      : 'Переподключить WSS без перезапуска Node, Agent и Xray?'
    if (!window.confirm(message)) return
    setReleaseBusy(true)
    try {
      await $fetch('/api/node-registry/nodes/' + encodeURIComponent(uuid) + '/operation', {
        method: 'POST',
        body: {
          action,
          expected_generation: releaseStates[uuid]?.generation ?? 0,
          ...(action === 'update' ? { image } : {}),
        },
      })
      toast.success(action === 'update' ? 'Обновление Node поставлено в очередь Agent' : 'Переподключение WSS запрошено')
      await loadNodeLifecycle(uuid)
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Не удалось запустить жизненный цикл Node')
      await loadNodeLifecycle(uuid)
    } finally { setReleaseBusy(false) }
  }

  const loadNodeRegistries = async (uuid: string) => {
    try {
      const result = await $fetch<{ registries: NodeRegistryProfile[]; default_repository: string }>(
        '/api/node-registry/nodes/' + encodeURIComponent(uuid) + '/registries')
      setRegistryProfiles(previous => ({ ...previous, [uuid]: result.registries }))
      setReleaseTargets(previous => previous[uuid] ? previous : {
        ...previous, [uuid]: withNodeReleaseRepository(result.default_repository),
      })
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Не удалось загрузить Registry этой Node')
    }
  }

  const saveNodeRegistry = async (uuid: string, draft: RegistryDraft) => {
    if (!canApprove || registryBusy || !draft.repository.trim()) return
    if (draft.repository.trim() === OFFICIAL_NODE_REPOSITORY) {
      toast.error('Общий доступ GHCR настраивается во вкладке «Токены»')
      return
    }
    setRegistryBusy(true)
    try {
      await $fetch('/api/node-registry/nodes/' + encodeURIComponent(uuid) + '/registries', {
        method: 'PUT', body: {
          repository: draft.repository.trim(), username: draft.username.trim(),
          ...(draft.password ? { password: draft.password } : {}),
        },
      })
      setRegistryDraft({ repository: '', username: '', password: '' })
      await loadNodeRegistries(uuid)
      toast.success('Registry сохранён для выбранной Node')
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Не удалось сохранить Registry')
    } finally { setRegistryBusy(false) }
  }

  const setDefaultNodeRegistry = async (uuid: string, repository: string) => {
    if (!canApprove || registryBusy) return
    setRegistryBusy(true)
    try {
      await $fetch('/api/node-registry/nodes/' + encodeURIComponent(uuid) + '/registries/default', {
        method: 'PUT', body: { repository },
      })
      setReleaseTargets(previous => ({ ...previous, [uuid]: withNodeReleaseRepository(repository) }))
      await loadNodeRegistries(uuid)
      toast.success('Registry выбран по умолчанию для этой Node')
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Не удалось выбрать основной Registry')
    } finally { setRegistryBusy(false) }
  }

  const deleteNodeRegistry = async (uuid: string, repository: string) => {
    if (!canApprove || registryBusy || !window.confirm('Удалить настройки этого Registry для выбранной Node?')) return
    setRegistryBusy(true)
    try {
      await $fetch('/api/node-registry/nodes/' + encodeURIComponent(uuid) + '/registries', {
        method: 'DELETE', body: { repository },
      })
      await loadNodeRegistries(uuid)
      setReleaseTargets(previous => (previous[uuid] ?? '').startsWith(repository + '@sha256:')
        ? { ...previous, [uuid]: withNodeReleaseRepository(OFFICIAL_NODE_REPOSITORY) } : previous)
      toast.success('Настройки Registry удалены для этой Node')
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Не удалось удалить Registry')
    } finally { setRegistryBusy(false) }
  }

  const activeReleaseStatus = releaseTarget ? releaseStates[releaseTarget]?.status : undefined
  useEffect(() => {
    if (!releaseTarget || !activeReleaseStatus ||
        !['pending', 'queued', 'running'].includes(activeReleaseStatus)) return
    const updateStatus = () => {
      if (document.visibilityState === 'visible') void loadNodeLifecycle(releaseTarget)
    }
    // Poll only the operator's active operation, then stop at terminal state.
    const interval = window.setInterval(updateStatus, 5000)
    return () => window.clearInterval(interval)
  }, [releaseTarget, activeReleaseStatus])

  const refresh = async () => {
    try {
      const result = await $fetch<{ nodes: Registration[] }>('/api/node-registry/nodes')
      setEntries(result.nodes)
      setError('')
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Не удалось загрузить узлы')
    }
  }

  useEffect(() => {
    if (!canRead) return
    void refresh()
    const refreshOnRegistration = () => { void refresh() }
    window.addEventListener('waylane:nodes-refresh', refreshOnRegistration)
    return () => window.removeEventListener('waylane:nodes-refresh', refreshOnRegistration)
  }, [canRead])

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


  const renameNode = async (uuid: string) => {
    if (!canApprove || renameBusy) return
    const name = nameEditors[uuid]?.trim()
    if (!name || name.length > 128) {
      toast.error('Имя ноды должно содержать от 1 до 128 символов')
      return
    }
    setRenameBusy(uuid)
    try {
      await $fetch('/api/node-registry/nodes/' + encodeURIComponent(uuid), {
        method: 'PATCH', body: { display_name: name },
      })
      setNameEditors(prev => { const next = { ...prev }; delete next[uuid]; return next })
      await refresh()
      toast.success('Название Node сохранено')
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Не удалось изменить название Node')
    } finally { setRenameBusy(null) }
  }

  const readXray = async (uuid: string) => {
    try {
      const state = await $fetch<XrayStatus>('/api/node-registry/nodes/' + encodeURIComponent(uuid) + '/xray')
      setXrayStates(prev => ({ ...prev, [uuid]: state }))
      setEditorCoreIds(prev => prev[uuid] ? prev : {
        ...prev, [uuid]: state.source_core_id ? String(state.source_core_id) : '',
      })
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

  const readXrayUsage = async (uuid: string) => {
    try {
      const values = await $fetch<XrayUsageState>(
        '/api/node-registry/nodes/' + encodeURIComponent(uuid) + '/xray/usage')
      setXrayUsageStates(previous => ({ ...previous, [uuid]: values }))
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Не удалось загрузить счётчики Xray')
    }
  }

  const readManagedInbounds = async (uuid: string) => {
    try {
      const response = await $fetch<{ inbound_tags: string[] }>(
        '/api/node-registry/nodes/' + encodeURIComponent(uuid) + '/xray/managed-inbounds')
      setManagedInboundStates(previous => ({ ...previous, [uuid]: response.inbound_tags }))
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Не удалось загрузить управление подписками')
    }
  }

  const changeManagedInbound = async (uuid: string, tag: string, enabled: boolean) => {
    if (!canManageXray) return
    const generation = xrayStates[uuid]?.generation
    if (!generation) {
      toast.error('Обновите состояние Xray перед изменением доступа')
      return
    }
    const warning = enabled
      ? `Разрешить WayLane управлять клиентами inbound ${tag}? Доступ будет определяться статусом подписки, entitlement и группами. Ручные клиенты не сохраняются.`
      : `Отключить автоматическую синхронизацию для ${tag}? Управляемые клиенты будут удалены при применении новой конфигурации.`
    if (!window.confirm(warning)) return
    setXrayBusy(true)
    try {
      await $fetch('/api/node-registry/nodes/' + encodeURIComponent(uuid) +
        '/xray/managed-inbounds/' + encodeURIComponent(tag), {
        method: enabled ? 'PUT' : 'DELETE',
        ...(enabled ? { body: { expected_generation: generation } }
          : { query: { expected_generation: generation } }),
      })
      toast.success('Изменение синхронизации подписок поставлено в очередь Node')
      await Promise.all([readManagedInbounds(uuid), readXray(uuid), readXrayInbounds(uuid)])
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Не удалось настроить автоматическую синхронизацию')
    } finally { setXrayBusy(false) }
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

  const readEditorCores = async () => {
    try {
      const list = await $fetch<{ cores: EditorCoreOption[] }>('/api/cores/simple?all=true')
      setEditorCores(list.cores.filter(core => core.type === 'xray'))
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Не удалось загрузить профили Xray из Core Editor')
    }
  }

  const applyEditorCore = async (uuid: string) => {
    if (!canManageXray) return
    const coreId = Number(editorCoreIds[uuid] ?? '')
    if (!Number.isSafeInteger(coreId) || coreId < 1) {
      toast.error('Сначала выберите сохранённый профиль Xray')
      return
    }
    setXrayBusy(true)
    try {
      await $fetch('/api/node-registry/nodes/' + encodeURIComponent(uuid) + '/xray/from-core', {
        method: 'PUT', body: {
          core_id: coreId,
          expected_generation: xrayStates[uuid]?.generation ?? 0,
        },
      })
      toast.success('Конфигурация Core Editor отправлена ноде через WSS')
      await readXray(uuid)
      await readXrayInbounds(uuid)
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Не удалось применить профиль Xray')
    } finally { setXrayBusy(false) }
  }

  const synchronizeEditorRouting = async (uuid: string) => {
    const generation = xrayStates[uuid]?.generation
    const coreId = Number(editorCoreIds[uuid] ?? '')
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
      inbounds: [{ tag: 'waylane-socks-qa', listen: '127.0.0.1', port: 18443,
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
    <Card className="m-4 min-w-0 shadow-sm">
      <CardHeader className="pb-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="space-y-1">
            <CardTitle>{t('nodes.connectedTitle')}</CardTitle>
            <CardDescription>{t('nodes.connectedDescription')}</CardDescription>
          </div>
          <Badge variant="outline" className="shrink-0">{entries.length} {t('nodes.countLabel')}</Badge>
        </div>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="flex items-center justify-between gap-2">
          <span className="text-sm font-medium">{t('nodes.registeredServers')}</span>
          <Button type="button" variant="outline" size="sm" onClick={() => void refresh()} disabled={loading}>Обновить</Button>
        </div>
        {error && <p role="alert" className="text-destructive text-sm">{error}</p>}
        {entries.length === 0 && !error && (
          <div className="rounded-lg border border-dashed px-4 py-8 text-center">
            <p className="mb-3 text-sm text-muted-foreground">{t('nodes.emptyHint')}</p>
            {canCreate && (
              <Button type="button" variant="outline"
                onClick={() => window.dispatchEvent(new Event('openNodeDialog'))}>
                {t('nodes.addNode')}
              </Button>
            )}
          </div>
        )}
        <div className="space-y-2">
          {entries.map(node => (
            <div key={node.node_uuid} className="min-w-0 space-y-4 rounded-xl border p-4 shadow-sm">
              <div className="min-w-0 space-y-3">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="min-w-0 break-words text-base font-semibold">{node.display_name}</span>
                  <Badge variant={node.status === 'approved' ? 'green' : node.status === 'pending' ? 'yellow' : 'red'}>
                    {t(`nodes.nodeStatus.${node.status}`, { defaultValue: node.status })}
                  </Badge>
                  <Badge variant="outline">{node.connection_mode === 'classic' ? 'Classic' : 'Discovery'}</Badge>
                </div>
                {canApprove && node.node_uuid in nameEditors && (
                  <div className="flex flex-wrap gap-2">
                    <Input aria-label="Новое название ноды" value={nameEditors[node.node_uuid]}
                      maxLength={128} className="max-w-xs"
                      onChange={event => setNameEditors(prev => ({ ...prev, [node.node_uuid]: event.target.value }))} />
                    <Button size="sm" disabled={renameBusy !== null}
                      onClick={() => void renameNode(node.node_uuid)}>Сохранить имя</Button>
                    <Button size="sm" variant="outline" disabled={renameBusy !== null}
                      onClick={() => setNameEditors(prev => { const next = { ...prev }; delete next[node.node_uuid]; return next })}>
                      Отмена
                    </Button>
                  </div>
                )}
                {canApprove && !(node.node_uuid in nameEditors) && (
                  <Button size="sm" variant="outline"
                    onClick={() => setNameEditors(prev => ({ ...prev, [node.node_uuid]: node.display_name }))}>
                    Переименовать
                  </Button>
                )}
                <p className="text-muted-foreground text-sm">
                  {node.last_seen_at ? `${t('nodes.lastSeen')}: ${new Date(node.last_seen_at).toLocaleString()}` : t('nodes.awaitingFirstHeartbeat')}
                  {' · '}{t('nodes.agentVersion')}: {node.node_version}
                </p>
                {canApprove && node.status !== 'revoked' && (
                  <div className="space-y-2 rounded-md bg-muted/30 p-3">
                    <div className="flex flex-wrap items-center gap-2">
                      <Button type="button" size="sm" variant="outline"
                        onClick={() => {
                          setReleaseTarget(releaseTarget === node.node_uuid ? null : node.node_uuid)
                          void loadNodeLifecycle(node.node_uuid)
                          if (releaseTarget !== node.node_uuid) {
                            setRegistryDraft({ repository: '', username: '', password: '' })
                            void loadNodeRegistries(node.node_uuid)
                          }
                        }}>
                        {releaseTarget === node.node_uuid ? t('nodes.releaseHide') : t('nodes.releaseSettings')}
                      </Button>
                      {!node.capabilities?.includes('node.release.v1') && (
                        <span className="text-muted-foreground text-xs">{t('nodes.releaseUnavailable')}</span>
                      )}
                    </div>
                    {releaseTarget === node.node_uuid && (
                      <div className="space-y-3">
                        <p className="text-muted-foreground text-xs">
                          Agent остаётся установленным. Изменяется только контейнер Node по SHA-256 digest
                          из разрешённого реестра. Новый контейнер должен подтвердить WSS; если этого не
                          произойдёт, Agent попытается восстановить предыдущую версию. Обновление Xray отдельно.
                        </p>
                        <div className="space-y-2 rounded-md border p-3">
                          <p className="text-sm font-medium">Registry этой Node</p>
                          <p className="text-muted-foreground text-xs">
                            Настройки сохраняются отдельно для каждой Node. Пароль шифруется на сервере и никогда не возвращается в панель.
                            Общий GHCR-токен задаётся во вкладке «Токены» и используется всеми нодами с GHCR.
                          </p>
                          {(registryProfiles[node.node_uuid] ?? [{ repository: OFFICIAL_NODE_REPOSITORY,
                            username: '', has_credentials: false, official: true, is_default: true }]).map(profile => (
                            <div key={profile.repository} className="flex flex-wrap items-center gap-2 rounded border p-2 text-xs">
                              <span className="min-w-0 flex-1 break-all font-mono">{profile.repository}</span>
                              <span>{profile.has_credentials
                                ? profile.credential_scope === 'shared' ? 'Общий GHCR доступ' : 'Авторизация настроена'
                                : 'Без авторизации'}</span>
                              {profile.is_default && <Badge variant="secondary">Основной</Badge>}
                              {canApprove && !profile.is_default && <Button size="sm" type="button" variant="outline"
                                disabled={registryBusy} onClick={() => void setDefaultNodeRegistry(node.node_uuid, profile.repository)}>
                                Сделать основным
                              </Button>}
                              {profile.official && <Link to="/nodes/tokens" className="text-primary underline underline-offset-2">
                                Настроить общий GHCR токен
                              </Link>}
                              {canApprove && !profile.official && <Button size="sm" type="button" variant="outline"
                                onClick={() => setRegistryDraft({ repository: profile.repository,
                                  username: profile.username, password: '' })}>Изменить</Button>}
                              {canApprove && (!profile.official || profile.credential_scope === 'node') && <Button size="sm" type="button"
                                variant="ghost" disabled={registryBusy}
                                onClick={() => void deleteNodeRegistry(node.node_uuid, profile.repository)}>
                                {profile.official ? 'Сбросить локальный доступ' : 'Удалить'}
                              </Button>}
                            </div>
                          ))}
                          {canApprove && <div className="space-y-2">
                            <Input aria-label="Репозиторий Registry этой Node" placeholder="registry.example.com/waylane/node"
                              value={registryDraft.repository} maxLength={55}
                              onChange={event => setRegistryDraft(previous => ({ ...previous, repository: event.target.value }))} />
                            <div className="grid gap-2 sm:grid-cols-2">
                              <Input aria-label="Логин Registry этой Node" placeholder="Логин (необязательно)"
                                value={registryDraft.username} maxLength={128} autoComplete="off"
                                onChange={event => setRegistryDraft(previous => ({ ...previous, username: event.target.value }))} />
                              <Input aria-label="Пароль Registry этой Node" placeholder="Пароль (пусто — оставить текущий)"
                                type="password" autoComplete="new-password" value={registryDraft.password} maxLength={1024}
                                onChange={event => setRegistryDraft(previous => ({ ...previous, password: event.target.value }))} />
                            </div>
                            <Button type="button" size="sm" disabled={registryBusy || !registryDraft.repository.trim() ||
                              registryDraft.repository.trim() === OFFICIAL_NODE_REPOSITORY}
                              onClick={() => void saveNodeRegistry(node.node_uuid, registryDraft)}>
                              Сохранить Registry для этой Node
                            </Button>
                          </div>}
                        </div>
                        <select aria-label="Registry для обновления Node"
                          className="border-input bg-background min-h-9 w-full rounded-md border px-3 py-1 text-sm"
                          value={(registryProfiles[node.node_uuid] ?? [{ repository: OFFICIAL_NODE_REPOSITORY, is_default: true }])
                            .find(profile => (releaseTargets[node.node_uuid] ?? '').startsWith(profile.repository + '@sha256:'))?.repository
                            ?? (registryProfiles[node.node_uuid]?.find(profile => profile.is_default)?.repository ?? OFFICIAL_NODE_REPOSITORY)}
                          onChange={event => setReleaseTargets(previous => ({ ...previous,
                            [node.node_uuid]: withNodeReleaseRepository(event.target.value),
                          }))}>
                          {(registryProfiles[node.node_uuid] ?? [{ repository: OFFICIAL_NODE_REPOSITORY }]).map(profile => (
                            <option key={profile.repository} value={profile.repository}>
                              {profile.repository === OFFICIAL_NODE_REPOSITORY ? 'GitHub Container Registry (GHCR)' : 'Собственный Registry'} — {profile.repository}
                            </option>
                          ))}
                        </select>
                        <p className="text-muted-foreground text-xs">У каждого Registry может быть свой SHA-256 digest. Вставьте digest выбранного образа.</p>
                        <Input aria-label="Новый digest Node" className="font-mono text-xs"
                          placeholder={OFFICIAL_NODE_REPOSITORY + '@sha256:...'}
                          value={releaseTargets[node.node_uuid] ?? ''}
                          onChange={e => setReleaseTargets(prev => ({ ...prev, [node.node_uuid]: e.target.value }))} />
                        <div className="flex flex-wrap gap-2">
                          <Button size="sm" disabled={releaseBusy || !node.capabilities?.includes('node.release.v1') ||
                            ['pending', 'queued', 'running'].includes(releaseStates[node.node_uuid]?.status ?? '')}
                            onClick={() => void requestNodeLifecycle(node.node_uuid, 'update')}>
                            Обновить вложенную Node
                          </Button>
                          <Button size="sm" variant="outline"
                            disabled={releaseBusy || !node.capabilities?.includes('node.reconnect.v1') ||
                              ['pending', 'queued', 'running'].includes(releaseStates[node.node_uuid]?.status ?? '')}
                            onClick={() => void requestNodeLifecycle(node.node_uuid, 'reconnect')}>
                            Переподключить WSS
                          </Button>
                          <Button size="sm" variant="ghost"
                            onClick={() => void loadNodeLifecycle(node.node_uuid)}>Обновить состояние</Button>
                        </div>
                        {releaseStates[node.node_uuid] && (
                          <div className="space-y-1 text-xs">
                            <p role="status">{releaseStates[node.node_uuid].action ?? 'Операций не было'} ·
                              {' '}{releaseStates[node.node_uuid].status} · {releaseStates[node.node_uuid].phase} ·
                              {' '}{releaseStates[node.node_uuid].progress}%</p>
                            <progress aria-label="Этапный прогресс обновления Node" className="w-full" max={100}
                              value={releaseStates[node.node_uuid].progress} />
                            <p className="text-muted-foreground">Процент обозначает этап операции, не объём скачанных данных.</p>
                            {releaseStates[node.node_uuid].image && (
                              <p className="break-all font-mono text-muted-foreground">Цель: {releaseStates[node.node_uuid].image}</p>
                            )}
                            {releaseStates[node.node_uuid].previous_image && (
                              <p className="break-all font-mono text-muted-foreground">Откат: {releaseStates[node.node_uuid].previous_image}</p>
                            )}
                            {releaseStates[node.node_uuid].error_code && (
                              <p role="alert" className="text-destructive">Ошибка: {releaseStates[node.node_uuid].error_code}</p>
                            )}
                          </div>
                        )}
                      </div>
                    )}
                  </div>
                )}
                <details className="border-t pt-3 text-xs text-muted-foreground">
                  <summary className="w-fit cursor-pointer rounded text-sm hover:text-foreground focus-visible:outline-2 focus-visible:outline-primary">
                    {t('nodes.technicalDetails')}
                  </summary>
                  <div className="mt-3 space-y-2 rounded-md bg-muted/30 p-3">
                    <p className="break-all font-mono">UUID: {node.node_uuid}</p>
                    <p className="break-all font-mono">{t('nodes.innerImage')}: {node.node_image ?? t('nodes.imageUnknown')} · Agent API v1</p>
                    <p>{t('nodes.connectionAddress')}: {node.observed_ip ?? '—'} · {t('nodes.publicAddress')}: {node.reported_ipv4 ?? node.reported_ipv6 ?? '—'}</p>
                    <p>{t('nodes.capabilities')}: {node.capabilities?.length ? node.capabilities.join(', ') : '—'}</p>
                    {node.machine_metrics && (
                      <p>{node.machine_metrics.os}/{node.machine_metrics.arch}
                        {' · '}CPU {node.machine_metrics.cpu_logical_count}
                        {' · '}RAM {Math.round(node.machine_metrics.memory_total_bytes / 1024 / 1024)} MiB
                        {' · '}load {node.machine_metrics.load_1.toFixed(2)}
                        {' · '}{node.machine_metrics.hostname}
                      </p>
                    )}
                  </div>
                </details>
              </div>
              {canManageXray && node.status === 'approved' && (
                <div className="min-w-0 w-full space-y-2 border-t pt-3">
                  <div className="flex flex-wrap items-center gap-2">
                    <Button size="sm" variant="outline" onClick={() => {
                      setXrayTarget(xrayTarget === node.node_uuid ? null : node.node_uuid)
                      void readXray(node.node_uuid)
                      void readXrayInbounds(node.node_uuid)
                      void readManagedInbounds(node.node_uuid)
                      void readXrayUsage(node.node_uuid)
                      void refreshTLSBindings(node.node_uuid)
                      void readEditorCores()
                      void $fetch<TLSPresetSummary[]>('/api/tls-presets')
                        .then(setTlsPresetChoices).catch(() => setTlsPresetChoices([]))
                    }} disabled={!node.capabilities?.includes('xray.protobuf.binary')}>Xray: управление (DEV)</Button>
                    <span className="text-muted-foreground text-xs">
                      {!node.capabilities?.includes('xray.protobuf.binary')
                        ? t('nodes.xrayNotSupported')
                        : !xrayStates[node.node_uuid]
                          ? t('nodes.xrayNotLoaded')
                          : xrayStates[node.node_uuid].configured
                            ? t('nodes.xrayRunningSummary', {
                                version: xrayStates[node.node_uuid].image_version,
                                status: t(`nodes.logs.status.${xrayStates[node.node_uuid].runtime_status}`, {
                                  defaultValue: xrayStates[node.node_uuid].runtime_status,
                                }),
                                applied: xrayStates[node.node_uuid].applied_generation,
                                generation: xrayStates[node.node_uuid].generation,
                              })
                            : t('nodes.xrayNotConfigured')}
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
                        <AlertDialog>
                          <AlertDialogTrigger asChild>
                            <Button variant="destructive" size="sm" disabled={xrayBusy || !xrayStates[node.node_uuid]?.configured}>Остановить</Button>
                          </AlertDialogTrigger>
                          <AlertDialogContent>
                            <AlertDialogHeader>
                              <AlertDialogTitle>Остановить Xray на этой ноде?</AlertDialogTitle>
                              <AlertDialogDescription>
                                VPN-подключения через это ядро будут прерваны. Действие не затрагивает другие ноды и ядра.
                              </AlertDialogDescription>
                            </AlertDialogHeader>
                            <AlertDialogFooter>
                              <AlertDialogCancel>Отмена</AlertDialogCancel>
                              <AlertDialogAction onClick={() => void changeXray(node.node_uuid, 'stop')}>Остановить Xray</AlertDialogAction>
                            </AlertDialogFooter>
                          </AlertDialogContent>
                        </AlertDialog>
                        <Button variant="outline" size="sm" disabled={xrayBusy}
                          onClick={() => void readXray(node.node_uuid)}>Статус</Button>
                      </div>
                      <div className="space-y-2 border-t pt-3">
                        <p className="text-sm font-medium">Профиль из Core Editor</p>
                        <p className="text-muted-foreground text-xs">
                          Выберите сохранённый профиль Xray по имени. Применение заменит конфигурацию
                          Xray на этой ноде и может прервать подключения. Другие ноды не затрагиваются.
                          Секреты управляемых пресетов не выводятся в браузер.
                        </p>
                        {xrayStates[node.node_uuid]?.source_core_id != null && (
                          <p className="text-muted-foreground text-xs">
                            Последний применённый профиль: <Link className="underline" to={'/nodes/cores/' + xrayStates[node.node_uuid].source_core_id}>
                              {editorCores.find(core => core.id === xrayStates[node.node_uuid]?.source_core_id)?.name ?? 'Открыть профиль'}
                            </Link>
                          </p>
                        )}
                        <div className="flex flex-wrap items-center gap-2">
                          <select aria-label="Сохранённый профиль Xray" className="border-input bg-background min-h-9 min-w-56 max-w-full rounded-md border px-3 py-1 text-sm"
                            value={editorCoreIds[node.node_uuid] ?? ''}
                            onChange={event => setEditorCoreIds(prev => ({ ...prev, [node.node_uuid]: event.target.value }))}>
                            <option value="">Выберите Xray Core</option>
                            {editorCores.map(core => <option key={core.id} value={core.id}>{core.name}</option>)}
                          </select>
                          {editorCoreIds[node.node_uuid] && <Link className="text-sm underline" to={'/nodes/cores/' + editorCoreIds[node.node_uuid]}>Открыть редактор</Link>}
                          <AlertDialog>
                            <AlertDialogTrigger asChild>
                              <Button size="sm" disabled={xrayBusy || !editorCoreIds[node.node_uuid] || !xrayStates[node.node_uuid]}>
                                Применить весь профиль к Xray
                              </Button>
                            </AlertDialogTrigger>
                            <AlertDialogContent>
                              <AlertDialogHeader>
                                <AlertDialogTitle>Применить конфигурацию к этой Node?</AlertDialogTitle>
                                <AlertDialogDescription>
                                  Работающее ядро Xray может быть перезапущено. Проверяйте клиентские подключения после применения.
                                </AlertDialogDescription>
                              </AlertDialogHeader>
                              <AlertDialogFooter>
                                <AlertDialogCancel>Отмена</AlertDialogCancel>
                                <AlertDialogAction onClick={() => void applyEditorCore(node.node_uuid)}>Применить конфигурацию</AlertDialogAction>
                              </AlertDialogFooter>
                            </AlertDialogContent>
                          </AlertDialog>
                        </div>
                      </div>
                      <div className="space-y-2 border-t pt-3">
                        <p className="text-sm font-medium">Только правила маршрутизации из Core Editor</p>
                        <p className="text-muted-foreground text-xs">
                          Использует сохранённые правила, balancers и параметры маршрутизации Xray Core Editor.
                          Остальные разделы текущего Xray не изменяются.
                        </p>
                        <div className="flex flex-wrap gap-2">
                          <span className="text-muted-foreground text-xs">{editorCores.find(core => String(core.id) === editorCoreIds[node.node_uuid])?.name ?? 'Выберите профиль выше'}</span>
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
                        <p className="text-sm font-medium">Пользователи Xray и коммерческие подписки</p>
                        <p className="text-muted-foreground text-xs">
                          Для VLESS, VMess и Trojan можно включить автоматическую синхронизацию.
                          WayLane будет допускать только оплаченные активные подписки из связанных групп,
                          используя UUID подписки в качестве технической идентичности. Выключенные, истёкшие
                          и превысившие лимиты подписки исключаются. Привязку к группе/inbound задайте в каталоге.
                          SOCKS5 и остальные протоколы по умолчанию остаются под ручным управлением.
                        </p>
                        <div className="space-y-2 text-xs text-muted-foreground">
                          {(xrayInboundStates[node.node_uuid] || []).map(inbound => {
                            const managed = managedInboundStates[node.node_uuid]?.includes(inbound.tag) ?? false
                            const supported = ['vless', 'vmess', 'trojan'].includes(inbound.protocol)
                            return (
                              <div key={inbound.tag} className="flex flex-wrap items-center justify-between gap-2 border-b pb-2">
                                <span className="break-all">
                                  {inbound.tag} · {inbound.protocol} · {managed
                                    ? 'управляется коммерческими подписками (актуальное количество только в WSS)'
                                    : `${inbound.user_count} ручных клиентов`}
                                  {!managed && inbound.identities.length ? ': ' + inbound.identities.join(', ') : ''}
                                </span>
                                {supported && <Button size="sm" variant="outline" disabled={xrayBusy || xrayStates[node.node_uuid]?.runtime_status !== 'running'}
                                  onClick={() => void changeManagedInbound(node.node_uuid, inbound.tag, !managed)}>
                                  {managed ? 'Отключить подписки' : 'Управлять подписками'}
                                </Button>}
                              </div>
                            )
                          })}
                        </div>
                        <p className="text-muted-foreground text-xs">
                          Ручная синхронизация ниже заменяет всех клиентов выбранного inbound.
                          Для inbound под управлением подписок она запрещена.
                        </p>
                        <Input aria-label="Xray inbound tag" placeholder="Tag inbound"
                          value={xrayInboundTag} onChange={e => setXrayInboundTag(e.target.value)} maxLength={128} />
                        <Textarea aria-label="Xray inbound users JSON" value={xrayUsersJSON}
                          rows={5} className="font-mono text-xs"
                          placeholder={'[{"user":"qa","pass":"strong-password"}]'}
                          onChange={e => setXrayUsersJSON(e.target.value)} />
                        <Button size="sm" variant="outline"
                          disabled={xrayBusy || !xrayUsersJSON.trim() || !xrayStates[node.node_uuid]?.configured ||
                            (managedInboundStates[node.node_uuid] || []).includes(xrayInboundTag.trim())}
                          onClick={() => void synchronizeInboundUsers(node.node_uuid)}>
                          Синхронизировать пользователей
                        </Button>
                      </div>
                      <div className="space-y-2 border-t pt-3">
                        <div className="flex flex-wrap items-center justify-between gap-2">
                          <p className="text-sm font-medium">Статистика Xray по UUID подписок</p>
                          <Button size="sm" variant="outline" disabled={xrayBusy}
                            onClick={() => void readXrayUsage(node.node_uuid)}>Обновить счётчики</Button>
                        </div>
                        <p className="text-muted-foreground text-xs">
                          Только наблюдение через приватный StatsService/WSS при явном разрешении
                          WAYLANE_XRAY_STATS_READONLY=true на Node. Отображаемые суммарные
                          счётчики не равны списанным коммерческим байтам: при включённом Backend
                          флаге биллинга учитываются только новые проверенные приращения.
                          Для получения чисел требуется включить
                          stats и statsUserUplink/statsUserDownlink в policy Xray, затем явно перезапустить
                          ядро для применения настроек.
                        </p>
                        <p className="text-muted-foreground text-xs">
                          Коммерческое начисление: {xrayUsageStates[node.node_uuid]?.billing_enabled
                            ? 'разрешено оператором Backend'
                            : 'выключено (только диагностика)'}.
                        </p>
                        {xrayUsageStates[node.node_uuid]?.samples.length === 0 && (
                          <p className="text-muted-foreground text-xs">Счётчики пока не поступали.</p>
                        )}
                        {xrayUsageStates[node.node_uuid]?.samples.slice(0, 20).map(sample => (
                          <div key={sample.subscription_uuid} className="min-w-0 border-b pb-2 text-xs">
                            <p className="break-all font-mono">{sample.subscription_uuid}</p>
                            <p className="text-muted-foreground">
                              ↑ {BigInt(sample.total_uplink).toLocaleString('ru-RU')} Б ·
                              {' '}↓ {BigInt(sample.total_downlink).toLocaleString('ru-RU')} Б ·
                              {' '}наблюдение {new Date(sample.observed_at).toLocaleString()}
                            </p>
                          </div>
                        ))}
                        {(xrayUsageStates[node.node_uuid]?.samples.length ?? 0) > 20 && (
                          <p className="text-muted-foreground text-xs">Показаны первые 20 записей из {xrayUsageStates[node.node_uuid]?.samples.length}.</p>
                        )}
                      </div>
                      {xrayStates[node.node_uuid]?.last_error && (
                        <p role="alert" className="text-destructive text-xs">{xrayStates[node.node_uuid].last_error}</p>
                      )}
                    </div>
                  )}
                </div>
              )}
              {canApprove && node.status !== 'revoked' && (
                <div className="flex flex-wrap justify-end gap-2 border-t pt-3">
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
