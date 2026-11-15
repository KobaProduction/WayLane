import PageHeader from '@/components/layout/page-header'
import PageTransition from '@/components/layout/page-transition'
import { useAdmin } from '@/hooks/use-admin'
import { getDocsUrl } from '@/utils/docs-url'
import { hasPermission } from '@/utils/rbac'
import { Cpu, LucideIcon, Share2, Plus, Logs, Network, KeyRound } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Outlet, useLocation, useNavigate } from 'react-router'
import { useCommandCreate } from '@/hooks/use-command-create'
import NodeOnboardingDialog from '@/features/nodes/components/node-onboarding-dialog'
import { getCoresListUseConfigModal } from '@/utils/dashboardPreferenceStorage'

interface Tab {
  id: string
  label: string
  icon: LucideIcon
  url: string
}

const tabs: Tab[] = [
  { id: 'nodes.title', label: 'nodes.title', icon: Share2, url: '/nodes' },
  { id: 'core', label: 'core', icon: Cpu, url: '/nodes/cores' },
  { id: 'nodes.wireguard.title', label: 'nodes.wireguard.title', icon: Network, url: '/nodes/wireguard' },
  { id: 'nodes.tokens.title', label: 'nodes.tokens.title', icon: KeyRound, url: '/nodes/tokens' },
  { id: 'nodes.logs.title', label: 'nodes.logs.title', icon: Logs, url: '/nodes/logs' },
]

const Settings = () => {
  const location = useLocation()
  const navigate = useNavigate()
  const { t } = useTranslation()
  const { admin } = useAdmin()
  const canReadNodes = hasPermission(admin, 'nodes', 'read')
  const canCreateNodes = hasPermission(admin, 'nodes', 'create')
  const canReadCores = hasPermission(admin, 'cores', 'read')
  const canCreateCores = hasPermission(admin, 'cores', 'create')
  const canReadNodeLogs = hasPermission(admin, 'nodes', 'logs')
  const visibleTabs = tabs.filter(tab => {
    if (tab.url === '/nodes') return canReadNodes
    if (tab.url === '/nodes/cores') return canReadCores
    if (tab.url === '/nodes/wireguard') return canReadCores
    if (tab.url === '/nodes/logs') return canReadNodeLogs
    if (tab.url === '/nodes/tokens') return canReadNodes
    return false
  })
  const [activeTab, setActiveTab] = useState<string>(tabs[0].id)
  const [createDialogOpen, setCreateDialogOpen] = useState(false)
  const isCoreEditorPage = /^\/nodes\/cores\/[^/]+$/.test(location.pathname)

  // A route change must never keep the onboarding modal above the logs/core
  // pages. Those views are independent operator tasks.
  useEffect(() => {
    if (location.pathname !== '/nodes') setCreateDialogOpen(false)
  }, [location.pathname])

  const handleCreateNode = useCallback(() => {
    if (!canReadNodes || !canCreateNodes) return
    setCreateDialogOpen(true)
  }, [canCreateNodes, canReadNodes])

  const handleCreateCore = useCallback(() => {
    if (!canCreateCores) return
    if (location.pathname === '/nodes/cores' && getCoresListUseConfigModal()) {
      window.dispatchEvent(new CustomEvent('openCoreDialog'))
      return
    }
    navigate('/nodes/cores/new')
  }, [canCreateCores, location.pathname, navigate])

  useCommandCreate('node', handleCreateNode)

  useEffect(() => {
    window.addEventListener('openNodeDialog', handleCreateNode)
    return () => window.removeEventListener('openNodeDialog', handleCreateNode)
  }, [handleCreateNode])
  useCommandCreate('core', handleCreateCore)

  useEffect(() => {
    if (location.pathname.startsWith('/nodes/cores')) {
      setActiveTab('core')
      return
    }
    const currentTab = tabs.find(tab => location.pathname === tab.url)
    if (currentTab) {
      setActiveTab(currentTab.id)
    }
  }, [location.pathname])

  useEffect(() => {
    if (isCoreEditorPage || visibleTabs.length === 0) return
    const currentTab = visibleTabs.find(tab => location.pathname === tab.url)
    if (!currentTab) {
      navigate(visibleTabs[0].url, { replace: true })
    }
  }, [isCoreEditorPage, location.pathname, navigate, visibleTabs])

  const getPageHeaderProps = () => {
    if (location.pathname.startsWith('/nodes/cores')) {
      return {
        title: 'settings.cores.title',
        description: 'settings.cores.description',
        buttonIcon: canCreateCores ? Plus : undefined,
        buttonText: canCreateCores ? 'settings.cores.addCore' : undefined,
        onButtonClick: canCreateCores
          ? handleCreateCore
          : undefined,
      }
    }
    if (location.pathname === '/nodes/wireguard') {
      return {
        title: 'nodes.wireguard.title',
        description: 'nodes.wireguard.description',
        buttonIcon: undefined,
        buttonText: undefined,
        onButtonClick: undefined,
      }
    }
    if (location.pathname === '/nodes/tokens') {
      return { title: 'nodes.tokens.title', description: 'nodes.tokens.description', buttonIcon: undefined, buttonText: undefined, onButtonClick: undefined }
    }
    if (location.pathname === '/nodes/logs') {
      return {
        title: 'nodes.logs.title',
        description: 'nodes.logs.description',
        buttonIcon: undefined,
        buttonText: undefined,
        onButtonClick: undefined,
      }
    }
    return {
      title: 'nodes.title',
      description: 'nodes.controlDescription',
      buttonIcon: canCreateNodes && canReadNodes ? Plus : undefined,
      buttonText: canCreateNodes && canReadNodes ? 'nodes.addNode' : undefined,
      onButtonClick: canReadNodes && canCreateNodes ? handleCreateNode : undefined,
    }
  }

  return (
    <div className="flex min-h-0 w-full flex-1 flex-col items-start gap-0">
      {!isCoreEditorPage && (
        <PageTransition isContentTransition={true}>
          <PageHeader {...getPageHeaderProps()} tutorialUrl={getDocsUrl(location.pathname)} />
        </PageTransition>
      )}
      <div className="flex min-h-0 w-full flex-1 flex-col">
        {!isCoreEditorPage && (
          <>
          <div className="border-b px-4 py-2 sm:hidden">
            <select aria-label={t('nodes.sectionSwitcher')}
              className="border-input bg-background min-h-10 w-full rounded-md border px-3 text-sm"
              value={visibleTabs.some(tab => tab.url === location.pathname) ? location.pathname :
                (location.pathname.startsWith('/nodes/cores') ? '/nodes/cores' : '/nodes')}
              onChange={event => navigate(event.target.value)}>
              {visibleTabs.map(tab => <option key={tab.id} value={tab.url}>{t(tab.label)}</option>)}
            </select>
          </div>
          <div className="scrollbar-hide hidden overflow-x-auto border-b px-4 sm:flex lg:flex-wrap">
            {visibleTabs.map(tab => (
              <button
                key={tab.id}
                onClick={() => navigate(tab.url)}
                className={`relative flex-shrink-0 px-3 py-2 text-sm font-medium whitespace-nowrap transition-colors ${
                  activeTab === tab.id ? 'border-primary text-foreground border-b-2' : 'text-muted-foreground hover:text-foreground'
                }`}
              >
                <div className="flex items-center gap-1.5">
                  <tab.icon className="h-4 w-4" />
                  {tab.id === 'core' ? (
                    <>
                      <span className="hidden sm:inline">{t(tab.label)}</span>
                      <span className="sm:hidden">{t('settings.cores.title')}</span>
                    </>
                  ) : (
                    <span>{t(tab.label)}</span>
                  )}
                </div>
              </button>
            ))}
          </div>
          </>
        )}
        <PageTransition isContentTransition={true} className="flex min-h-0 flex-1 flex-col">
          <Outlet />
        </PageTransition>
        {canReadNodes && canCreateNodes && (
          <NodeOnboardingDialog
            open={createDialogOpen}
            onOpenChange={setCreateDialogOpen}
            onCreated={() => window.dispatchEvent(new Event('waylane:nodes-refresh'))}
          />
        )}
      </div>
    </div>
  )
}

export default Settings
