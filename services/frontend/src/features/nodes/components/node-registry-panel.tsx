import { useEffect, useState } from 'react'
import { validRegistrationFilter } from './node-registry-overview'
import type { NodeRegistration, NodeWssObservation, NodeWssSummary } from './node-registry-types'
import { observedWssPresence } from './node-wss-presence'
import { NodeReleaseStatus, type NodeReleaseOperation } from './node-release-status'
import { NodeRegistryCredentials, type NodeRegistryProfile, type RegistryDraft } from './node-registry-credentials'
import { NodeTLSBindings, type AgentTLSBinding, type TLSPresetSummary } from './node-tls-bindings'
import { NodeXrayUsage, type XrayUsageState } from './node-xray-usage'
import { NodeXrayRuntimeControls, type XrayCommand } from './node-xray-runtime-controls'
import { NodeXrayCoreAssignment } from './node-xray-core-assignment'
import { NodeXrayClientManagement } from './node-xray-client-management'
import type { XrayInboundInfo, XrayStatus, EditorCoreOption } from './node-xray-types'
import { useTranslation } from 'react-i18next'
import { Link, useSearchParams } from 'react-router'
import { NODE_DETAIL_TABS, validNodeDetailTab } from './node-detail-tabs'
import { $fetch } from '@/service/http'
import { useAdmin } from '@/hooks/use-admin'
import { hasPermission } from '@/utils/rbac'
import { toast } from 'sonner'
import { OFFICIAL_NODE_REPOSITORY, approvedNodeReleaseImage, withNodeReleaseRepository } from './node-release-image'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Badge } from '@/components/ui/badge'
import { AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle, AlertDialogTrigger } from '@/components/ui/alert-dialog'

/** Autonomous WayLane Node registration and supported core controls. */
export default function NodeDiscoveryPanel({ detailNodeUuid }: { detailNodeUuid: string }) {
  const { admin } = useAdmin()
  const [searchParams] = useSearchParams()
  const detailTab = validNodeDetailTab(searchParams.get('tab'))
  const returnStatus = validRegistrationFilter(searchParams.get('returnStatus'))
  const { t } = useTranslation()
  const canRead = hasPermission(admin, 'nodes', 'read')
  const canReadWss = hasPermission(admin, 'nodes', 'stats')
  const canApprove = hasPermission(admin, 'nodes', 'update')
  const [entries, setEntries] = useState<NodeRegistration[]>([])
  const currentNode = entries.find(node => node.node_uuid === detailNodeUuid)
  const returnUrl = returnStatus === 'all' ? '/nodes' : `/nodes?status=${returnStatus}`
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
  const [initialLoaded, setInitialLoaded] = useState(false)
  const [error, setError] = useState('')
  const [observations, setObservations] = useState<NodeWssObservation[] | null>(null)
  const [wssCheckedAt, setWssCheckedAt] = useState<number | null>(null)
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

  const refresh = async () => {
    try {
      const result = await $fetch<{ nodes: NodeRegistration[] }>('/api/node-registry/nodes')
      setEntries(result.nodes)
      setError('')
      setObservations(null)
      setWssCheckedAt(null)
      if (canReadWss) {
        try {
          const latest = await $fetch<NodeWssSummary>('/api/node-registry/analytics/summary')
          setObservations(latest.nodes)
          setWssCheckedAt(Date.now())
        } catch { /* Read-only WSS analytics is optional; unknown is not disconnected. */ }
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Не удалось загрузить узлы')
    } finally {
      setInitialLoaded(true)
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
      toast.success('Применение Xray Core поставлено в очередь Node. Подтверждение применения проверьте в статусе Xray.')
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
      toast.success('TLS-пресет привязан, обновление Xray поставлено в очередь. Проверьте поколение применения.')
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

  const changeXray = async (uuid: string, action: XrayCommand) => {
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
      toast.success('Операция Xray поставлена в очередь. Результат будет доступен после подтверждения Node.')
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
      toast.success('Синхронизация клиентов Xray поставлена в очередь. Проверьте применение на Node.')
      await readXray(uuid)
      await readXrayInbounds(uuid)
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Ошибка синхронизации пользователей Xray')
    } finally { setXrayBusy(false) }
  }

  // Load only the currently opened Node section; do not prefetch other Nodes or start a new watcher.
  useEffect(() => {
    if (!detailNodeUuid || currentNode?.status !== 'approved') {
      setReleaseTarget(null)
      setXrayTarget(null)
      return
    }
    if (detailTab === 'operations' || detailTab === 'registry') {
      setReleaseTarget(detailNodeUuid)
      if (detailTab === 'operations') void loadNodeLifecycle(detailNodeUuid)
      void loadNodeRegistries(detailNodeUuid)
    } else {
      setReleaseTarget(null)
    }
    if ((detailTab === 'xray' || detailTab === 'tls' || detailTab === 'diagnostics') &&
        currentNode.capabilities?.includes('xray.protobuf.binary') && canManageXray) {
      setXrayTarget(detailNodeUuid)
      void readXray(detailNodeUuid)
      if (detailTab === 'xray') {
        void readXrayInbounds(detailNodeUuid)
        void readManagedInbounds(detailNodeUuid)
        void readEditorCores()
      }
      if (detailTab === 'tls') {
        void refreshTLSBindings(detailNodeUuid)
        void $fetch<TLSPresetSummary[]>('/api/tls-presets').then(setTlsPresetChoices).catch(() => setTlsPresetChoices([]))
      }
      if (detailTab === 'diagnostics') void readXrayUsage(detailNodeUuid)
    } else {
      setXrayTarget(null)
    }
  }, [detailNodeUuid, detailTab, currentNode?.node_uuid, currentNode?.status, canManageXray])

  if (!canRead) return <p role="alert" className="px-4 py-6 text-sm text-muted-foreground">{t('nodes.details.noAccess', { defaultValue: 'Недостаточно прав для просмотра Node.' })}</p>

  return (
    <Card className="m-4 min-w-0 border-0 shadow-none">
      <CardHeader className="space-y-4 px-0 pb-3">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="space-y-1">
            <Link to={returnUrl} className="text-sm text-primary underline underline-offset-4">{t('nodes.details.back', { defaultValue: 'К списку узлов' })}</Link>
            <CardTitle>{currentNode?.display_name ?? t('nodes.details.title', { defaultValue: 'Узел' })}</CardTitle>
            <CardDescription>{t('nodes.details.description', { defaultValue: 'Регистрация, настройки и результаты управления конкретной Node.' })}</CardDescription>
          </div>
          <Button type="button" variant="outline" size="sm" onClick={() => void refresh()} disabled={loading}>
            {t('nodes.registryOverview.refresh', { defaultValue: 'Обновить список' })}
          </Button>
        </div>
        <nav aria-label={t('nodes.details.tabsTitle', { defaultValue: 'Разделы узла' })} className="flex flex-wrap gap-1 border-b pb-2">
          {NODE_DETAIL_TABS.map(tab => <Link key={tab}
            to={`?tab=${tab}${returnStatus === 'all' ? '' : `&returnStatus=${returnStatus}`}`} aria-current={detailTab === tab ? 'page' : undefined}
            className={`rounded-md px-3 py-2 text-sm font-medium transition-colors focus-visible:ring-2 focus-visible:ring-ring ${detailTab === tab ? 'bg-primary text-primary-foreground' : 'text-muted-foreground hover:bg-muted hover:text-foreground'}`}>
            {t(`nodes.details.tabs.${tab}`, { defaultValue: tab })}
          </Link>)}
        </nav>
      </CardHeader>
      <CardContent className="space-y-4 px-0">
        {error && <p role="alert" className="text-destructive text-sm">{error}</p>}
        {!initialLoaded && !error && <p role="status" className="text-sm text-muted-foreground">{t('nodes.details.loading', { defaultValue: 'Загрузка данных Node…' })}</p>}
        {initialLoaded && !currentNode && !error && <div className="rounded-lg border border-dashed p-6 text-sm text-muted-foreground">
          {t('nodes.details.notFound', { defaultValue: 'Node не найдена в реестре.' })}
          <Link to={returnUrl} className="ml-2 text-primary underline">{t('nodes.details.back', { defaultValue: 'К списку узлов' })}</Link>
        </div>}
        <div className="space-y-3">
          {entries.filter(node => node.node_uuid === detailNodeUuid).map(node => (
            <div key={node.node_uuid} className="min-w-0 space-y-4 rounded-xl border bg-card p-4">
              <div className="min-w-0 space-y-3">
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div className="min-w-0 space-y-2">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="min-w-0 break-words text-base font-semibold">{node.display_name}</span>
                      <Badge variant={node.status === 'approved' ? 'outline' : node.status === 'pending' ? 'yellow' : 'red'}>
                        {t(`nodes.nodeStatus.${node.status}`, { defaultValue: node.status })}
                      </Badge>
                      <Badge variant="secondary">{node.connection_mode === 'classic' ? 'Classic' : 'Discovery'}</Badge>
                    </div>
                    <p className="text-muted-foreground text-sm">
                      {node.last_seen_at ? `${t('nodes.lastSeen')}: ${new Date(node.last_seen_at).toLocaleString()}` : t('nodes.awaitingFirstHeartbeat')}
                      {' · '}{t('nodes.agentVersion')}: {node.node_version}
                    </p>
                    <p className="text-xs text-muted-foreground" aria-label="WSS: последний подтверждённый сигнал">
                      {observedWssPresence(node.node_uuid, wssCheckedAt, observations) === 'recent'
                        ? t('nodes.registryOverview.wssRecent', { defaultValue: 'WSS: heartbeat был свежим при проверке (до 90 с)' })
                        : observedWssPresence(node.node_uuid, wssCheckedAt, observations) === 'stale'
                          ? t('nodes.registryOverview.wssStale', { defaultValue: 'WSS: свежий heartbeat не получен при проверке' })
                          : t('nodes.registryOverview.wssUnknown', { defaultValue: 'WSS-сигнал не проверен' })}
                      {wssCheckedAt !== null && ` · ${new Date(wssCheckedAt).toLocaleTimeString()}`}
                    </p>
                  </div>
                  {detailTab === 'overview' && canApprove && !(node.node_uuid in nameEditors) && (
                    <Button size="sm" variant="outline" className="shrink-0"
                      onClick={() => setNameEditors(prev => ({ ...prev, [node.node_uuid]: node.display_name }))}>
                      {t('nodes.registryOverview.rename', { defaultValue: 'Переименовать' })}
                    </Button>
                  )}
                </div>
                {detailTab === 'overview' && canApprove && node.node_uuid in nameEditors && (
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

                {detailTab === 'overview' && (
                  <div className="space-y-3 border-t pt-3">
                    <p className="text-sm text-muted-foreground">
                      {t('nodes.details.registrationNote', { defaultValue: 'Статус регистрации не гарантирует активного соединения WSS или работы VPN.' })}
                    </p>
                    <div className="flex flex-wrap gap-2">
                      <Link to={`?tab=operations${returnStatus === 'all' ? '' : `&returnStatus=${returnStatus}`}`} className="rounded-md border px-3 py-2 text-sm hover:bg-muted">{t('nodes.details.tabs.operations', { defaultValue: 'Операции' })}</Link>
                      <Link to={`?tab=xray${returnStatus === 'all' ? '' : `&returnStatus=${returnStatus}`}`} className="rounded-md border px-3 py-2 text-sm hover:bg-muted">Xray</Link>
                      <Link to={`?tab=diagnostics${returnStatus === 'all' ? '' : `&returnStatus=${returnStatus}`}`} className="rounded-md border px-3 py-2 text-sm hover:bg-muted">{t('nodes.details.tabs.diagnostics', { defaultValue: 'Диагностика' })}</Link>
                    </div>
                  </div>
                )}
                {detailTab !== 'overview' && node.status !== 'approved' && <p role="status" className="text-sm text-muted-foreground">
                  {t('nodes.details.notApproved', { defaultValue: 'Операции доступны после подтверждения Node.' })}
                </p>}
                {(detailTab === 'operations' || detailTab === 'registry') && node.status === 'approved' && !canApprove && <p role="status" className="text-sm text-muted-foreground">
                  {t('nodes.details.noRegistryAccess', { defaultValue: 'Для управления Registry необходимы права изменения Node.' })}
                </p>}
                {(detailTab === 'xray' || detailTab === 'tls' || detailTab === 'diagnostics') && node.status === 'approved' &&
                  (!canManageXray || !node.capabilities?.includes('xray.protobuf.binary')) && <p role="status" className="text-sm text-muted-foreground">
                    {t('nodes.details.xrayUnavailable', { defaultValue: 'Управление Xray на этой Node недоступно или не разрешено.' })}
                  </p>}

                {(detailTab === 'operations' || detailTab === 'registry') && canApprove && node.status === 'approved' && (
                  <div className="space-y-2 border-t pt-3">
                    {detailTab === 'operations' && <div className="flex flex-wrap items-center justify-between gap-2">
                      <p className="text-sm font-medium">Жизненный цикл Node</p>
                      <Button type="button" size="sm" variant="outline" onClick={() => void loadNodeLifecycle(node.node_uuid)}>
                        Обновить состояние
                      </Button>
                      {!node.capabilities?.includes('node.release.v1') && (
                        <span className="text-muted-foreground text-xs">{t('nodes.releaseUnavailable')}</span>
                      )}
                    </div>}
                    {releaseTarget === node.node_uuid && (
                      <div className="space-y-3">
                        {detailTab === 'operations' && <p className="text-muted-foreground text-xs">
                          Обновляется только внутренний контейнер Node по SHA-256 digest; Agent остаётся установленным.
                          В случае неуспешного WSS Agent может откатить образ. Изменение Xray выполняется отдельно.
                        </p>}
                        {detailTab === 'registry' && <NodeRegistryCredentials
                          profiles={registryProfiles[node.node_uuid]}
                          draft={registryDraft}
                          setDraft={setRegistryDraft}
                          busy={registryBusy}
                          canUpdate={canApprove}
                          onSave={draft => void saveNodeRegistry(node.node_uuid, draft)}
                          onSetDefault={repository => void setDefaultNodeRegistry(node.node_uuid, repository)}
                          onDelete={repository => void deleteNodeRegistry(node.node_uuid, repository)}
                        />}
                        {detailTab === 'operations' && <>
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
                        <NodeReleaseStatus operation={releaseStates[node.node_uuid]} />
                        </>}
                      </div>
                    )}
                  </div>
                )}
                { detailTab === 'diagnostics' && <section className="border-t pt-3 text-xs text-muted-foreground">
                  <h3 className="mb-2 text-sm font-semibold text-foreground">{t('nodes.technicalDetails')}</h3>
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
                </section>}
              </div>
              { (detailTab === 'xray' || detailTab === 'tls' || detailTab === 'diagnostics') && canManageXray && node.status === 'approved' && (
                <div className="min-w-0 w-full space-y-2 border-t pt-3">
                  <div className="flex flex-wrap items-center gap-2">
                    <Button size="sm" variant="outline" onClick={() => {
                      void readXray(node.node_uuid)
                      if (detailTab === 'tls') void refreshTLSBindings(node.node_uuid)
                      if (detailTab === 'diagnostics') void readXrayUsage(node.node_uuid)
                    }} disabled={!node.capabilities?.includes('xray.protobuf.binary')}>Обновить данные Xray</Button>
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
                      {detailTab === 'xray' && <div className="space-y-4">
                        <NodeXrayCoreAssignment
                          cores={editorCores}
                          selectedCoreId={editorCoreIds[node.node_uuid] ?? ''}
                          onCoreChange={value => setEditorCoreIds(previous => ({ ...previous, [node.node_uuid]: value }))}
                          current={xrayStates[node.node_uuid]}
                          busy={xrayBusy}
                          onApply={() => void applyEditorCore(node.node_uuid)}
                          onApplyRouting={() => void synchronizeEditorRouting(node.node_uuid)}
                        />
                        <NodeXrayRuntimeControls
                          imageVersion={xrayVersion}
                          region={xrayRegion}
                          configuration={xrayConfig}
                          configured={Boolean(xrayStates[node.node_uuid]?.configured)}
                          busy={xrayBusy}
                          onImageVersionChange={setXrayVersion}
                          onRegionChange={setXrayRegion}
                          onConfigurationChange={setXrayConfig}
                          onGenerateSample={demoXray}
                          onCommand={action => void changeXray(node.node_uuid, action)}
                          onRefresh={() => void readXray(node.node_uuid)}
                        />
                      </div>}
                      {detailTab === 'tls' && <NodeTLSBindings
                        bindings={agentTLSBindings[node.node_uuid] ?? []}
                        presets={tlsPresetChoices}
                        inboundTag={tlsInboundTag}
                        onInboundTagChange={setTlsInboundTag}
                        selectedPresetId={tlsPresetId}
                        onPresetChange={setTlsPresetId}
                        canBind={canBindTLS}
                        busy={xrayBusy}
                        configured={Boolean(xrayStates[node.node_uuid]?.configured)}
                        onBind={() => void bindExistingTLS(node.node_uuid)}
                      />}
                      {detailTab === 'xray' && <NodeXrayClientManagement
                        inbounds={xrayInboundStates[node.node_uuid] ?? []}
                        managedInboundTags={managedInboundStates[node.node_uuid] ?? []}
                        runtime={xrayStates[node.node_uuid]}
                        inboundTag={xrayInboundTag}
                        onInboundTagChange={setXrayInboundTag}
                        usersJson={xrayUsersJSON}
                        onUsersJsonChange={setXrayUsersJSON}
                        busy={xrayBusy}
                        onManageInbound={(tag, enabled) => void changeManagedInbound(node.node_uuid, tag, enabled)}
                        onSyncUsers={() => void synchronizeInboundUsers(node.node_uuid)}
                      />}
                      {detailTab === 'diagnostics' && <NodeXrayUsage
                        data={xrayUsageStates[node.node_uuid]}
                        busy={xrayBusy}
                        onRefresh={() => void readXrayUsage(node.node_uuid)}
                      />}
                      {xrayStates[node.node_uuid]?.last_error && (
                        <p role="alert" className="text-destructive text-xs">{xrayStates[node.node_uuid].last_error}</p>
                      )}
                    </div>
                  )}
                </div>
              )}
              {detailTab === 'overview' && canApprove && node.status !== 'revoked' && (
                <div className="flex flex-wrap justify-end gap-2 border-t pt-3">
                  {node.status === 'pending' && <Button size="sm" disabled={loading} onClick={() => void decide(node.node_uuid, 'approve')}>Подтвердить</Button>}
                  <AlertDialog>
                    <AlertDialogTrigger asChild>
                      <Button variant="destructive" size="sm" disabled={loading}>Отозвать регистрацию</Button>
                    </AlertDialogTrigger>
                    <AlertDialogContent>
                      <AlertDialogHeader>
                        <AlertDialogTitle>Отозвать регистрацию этой Node?</AlertDialogTitle>
                        <AlertDialogDescription>
                          Node потеряет разрешение на управление через WayLane. Отозванную регистрацию нельзя подтвердить повторно — понадобится новая регистрация Node.
                          Agent и файлы на сервере этой операцией не удаляются.
                        </AlertDialogDescription>
                      </AlertDialogHeader>
                      <AlertDialogFooter>
                        <AlertDialogCancel>Отмена</AlertDialogCancel>
                        <AlertDialogAction disabled={loading} onClick={() => void decide(node.node_uuid, 'revoke')}>Отозвать</AlertDialogAction>
                      </AlertDialogFooter>
                    </AlertDialogContent>
                  </AlertDialog>
                </div>
              )}
            </div>
          ))}
        </div>
      </CardContent>
    </Card>
  )
}
