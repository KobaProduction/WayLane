import AdminStatisticsCard from '@/features/dashboard/components/admin-statistics-card'
import DashboardStatistics from '@/features/dashboard/components/dashboard-statistics'
import WorkersHealthCard from '@/features/dashboard/components/workers-health-card'
import AdminFilterCombobox from '@/components/common/admin-filter-combobox'
import { useCommandPaletteStore } from '@/hooks/use-command-palette-store'
import { Separator } from '@/components/ui/separator'
import { useAdmin } from '@/hooks/use-admin'
import type { AdminDetails } from '@/service/api'
import { useGetSystemSubscriptionsStats } from '@/service/api'
import { Bookmark } from 'lucide-react'
import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router'
import PageHeader from '@/components/layout/page-header'
import { hasPermission, hasScopeAll } from '@/utils/rbac'
import { useQueryClient } from '@tanstack/react-query'

type DashboardAdmin = Pick<AdminDetails, 'id' | 'username'>

const totalAdmin: DashboardAdmin = {
  username: 'Total',
}

const Dashboard = () => {
  const setCommandPaletteOpen = useCommandPaletteStore(s => s.setOpen)
  const { admin: currentAdmin } = useAdmin()
  const canReadAllUsers = hasScopeAll(currentAdmin, 'subscriptions', 'read')
  const canCreateSubscriptions = hasPermission(currentAdmin, 'subscriptions', 'create')
  const canReadNodeStats = hasPermission(currentAdmin, 'nodes', 'stats')

  const [selectedAdmin, setSelectedAdmin] = useState<DashboardAdmin | undefined>(totalAdmin)

  const queryClient = useQueryClient()
  const navigate = useNavigate()

  const refreshAllUserData = () => {
    queryClient.invalidateQueries({ queryKey: ['getUsers'] })
    queryClient.invalidateQueries({ queryKey: ['getSubscriptionUsageMetrics'] })
    queryClient.invalidateQueries({ queryKey: ['waylane-product-subscriptions'] })
  }

  const handleCreateSubscription = () => {
    if (!canCreateSubscriptions) return
    navigate('/subscriptions')
  }

  const handleOpenQuickActions = () => {
    setCommandPaletteOpen(true)
  }

  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'n' && (event.metaKey || event.ctrlKey)) {
        event.preventDefault()
        handleCreateSubscription()
      }
      if (event.key === 'r' && (event.metaKey || event.ctrlKey)) {
        event.preventDefault()
        refreshAllUserData()
      }
    }

    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [])

  const systemUsersStatsParams = canReadAllUsers && selectedAdmin && selectedAdmin.username !== 'Total' ? { admin_username: selectedAdmin.username } : undefined

  const { data: systemUsersStatsData } = useGetSystemSubscriptionsStats(systemUsersStatsParams, {
    query: {
      refetchInterval: 5000,
    },
  })

  return (
    <div className="flex w-full flex-col items-start gap-2">
      <div className="w-full">
        <PageHeader title="dashboard" description="dashboardDescription" buttonIcon={Bookmark} buttonText="quickActions.title" onButtonClick={handleOpenQuickActions} />
        <Separator />
      </div>

      <div className="w-full px-3 pt-2 sm:px-4">
        <div className="flex flex-col gap-4 sm:gap-6">
          <DashboardStatistics usersData={systemUsersStatsData} />
          {canReadNodeStats && <WorkersHealthCard />}
          <Separator className="my-4" />
          {canReadAllUsers ? (
            <>
              <AdminFilterCombobox
                value={selectedAdmin?.username === 'Total' ? 'all' : (selectedAdmin?.username ?? 'all')}
                onValueChange={username => {
                  if (username === 'all') {
                    setSelectedAdmin(totalAdmin)
                    return
                  }
                  if (currentAdmin?.username === username) {
                    setSelectedAdmin(currentAdmin)
                    return
                  }
                  setSelectedAdmin(prev => (prev?.username === username ? prev : { username }))
                }}
                onAdminSelect={admin => {
                  if (!admin) return
                  setSelectedAdmin(admin)
                }}
                className="relative mb-3 w-full max-w-xs sm:mb-4 sm:max-w-sm lg:max-w-md"
              />
              <div className="flex flex-col gap-3 sm:gap-4">
                {selectedAdmin && <AdminStatisticsCard key={selectedAdmin.username} admin={selectedAdmin} systemStats={systemUsersStatsData} currentAdmin={currentAdmin} skipStatsFetch />}
              </div>
            </>
          ) : (
            <AdminStatisticsCard showAdminInfo={false} admin={currentAdmin} systemStats={systemUsersStatsData} currentAdmin={currentAdmin} skipStatsFetch />
          )}
        </div>
      </div>

    </div>
  )
}

export default Dashboard
