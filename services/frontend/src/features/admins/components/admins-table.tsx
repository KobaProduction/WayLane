import { useTranslation } from 'react-i18next'
import type { AdminDetails } from '@/service/api'
import {
  useBulkDeleteAdmins,
  useBulkDisableAdmins,
  useBulkEnableAdmins,
  useBulkResetAdminsUsage,
  useGetAdmins,
} from '@/service/api'
import { DataTable } from './data-table'
import { setupColumns } from './columns'
import { Filters } from './filters'
import { useEffect, useState, useRef, useCallback } from 'react'
import { PaginationControls } from './filters'
import { AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle } from '@/components/ui/alert-dialog'
import useDirDetection from '@/hooks/use-dir-detection'
import { getAdminsPerPageLimitSize, setAdminsPerPageLimitSize } from '@/utils/dashboardPreferenceStorage'
import { toast } from 'sonner'
import { useAdmin } from '@/hooks/use-admin'
import { useQueryClient } from '@tanstack/react-query'
import { BulkActionItem, BulkActionsBar } from '@/components/common/bulk-actions-bar'
import { BulkActionAlertDialog } from '@/components/common/bulk-action-alert-dialog'
import { Power, PowerOff, RefreshCw, Trash2 } from 'lucide-react'
import { hasPermission } from '@/utils/rbac'
import { translateHtml } from '@/utils/html-translation'

interface AdminFilters {
  sort?: string
  username?: string | null
  limit: number
  offset: number
}

interface AdminsTableProps {
  onEdit: (admin: AdminDetails) => void
  onDelete: (admin: AdminDetails) => void
  onToggleStatus: (admin: AdminDetails) => void
  onResetUsage: (admin: AdminDetails) => void
  onTotalAdminsChange?: (counts: { total: number; active: number; disabled: number; limited: number } | null) => void
}

type BulkAdminActionType = 'delete' | 'reset' | 'disable' | 'enable'

interface BulkActionDialogConfig {
  title: string
  description: string
  actionLabel: string
  onConfirm: () => Promise<void>
  isPending: boolean
  destructive?: boolean
}

const compactAdminIds = (admins: AdminDetails[]): number[] => admins.map(admin => admin.id).filter((id): id is number => typeof id === 'number')
const getAdminStatus = (admin: AdminDetails) => admin.status || (admin.is_disabled ? 'disabled' : 'active')

const DeleteAlertDialog = ({ admin, isOpen, onClose, onConfirm }: { admin: AdminDetails; isOpen: boolean; onClose: () => void; onConfirm: () => void }) => {
  const { t } = useTranslation()
  const dir = useDirDetection()

  return (
    <AlertDialog open={isOpen} onOpenChange={onClose}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>{t('admins.deleteAdmin')}</AlertDialogTitle>
          <AlertDialogDescription>
            <span dir={dir} dangerouslySetInnerHTML={{ __html: translateHtml(t, 'deleteAdmin.prompt', { name: admin.username }) }} />
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel onClick={onClose}>{t('cancel')}</AlertDialogCancel>
          <AlertDialogAction variant="destructive" onClick={onConfirm}>
            {t('delete')}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  )
}

const ToggleAdminStatusModal = ({ admin, isOpen, onClose, onConfirm }: { admin: AdminDetails; isOpen: boolean; onClose: () => void; onConfirm: () => void }) => {
  const { t } = useTranslation()
  const dir = useDirDetection()
  const isDisabled = getAdminStatus(admin) === 'disabled'

  return (
    <AlertDialog open={isOpen} onOpenChange={onClose}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>{t(isDisabled ? 'admin.enable' : 'admin.disable')}</AlertDialogTitle>
          <AlertDialogDescription>
            <span
              dir={dir}
              dangerouslySetInnerHTML={{
                __html: translateHtml(t, isDisabled ? 'admin.enablePrompt' : 'admin.disablePrompt', {
                  name: admin.username,
                  defaultValue: isDisabled ? 'Are you sure you want to enable admin <b>{{name}}</b>?' : 'Are you sure you want to disable admin <b>{{name}}</b>?',
                }),
              }}
            />
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel onClick={onClose}>{t('cancel')}</AlertDialogCancel>
          <AlertDialogAction onClick={onConfirm}>{t('confirm')}</AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  )
}

const ResetUsersUsageConfirmationDialog = ({ adminUsername, isOpen, onClose, onConfirm }: { adminUsername: string; isOpen: boolean; onClose: () => void; onConfirm: () => void }) => {
  const { t } = useTranslation()
  const dir = useDirDetection()

  return (
    <AlertDialog open={isOpen} onOpenChange={onClose}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>{t('admins.resetUsersUsage')}</AlertDialogTitle>
          <AlertDialogDescription className="flex items-center gap-2">
            <span dir={dir} dangerouslySetInnerHTML={{ __html: translateHtml(t, 'resetUsersUsage.prompt', { name: adminUsername }) }} />
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel onClick={onClose}>{t('cancel')}</AlertDialogCancel>
          <AlertDialogAction onClick={onConfirm}>{t('confirm')}</AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  )
}

export default function AdminsTable({ onEdit, onDelete, onToggleStatus, onResetUsage, onTotalAdminsChange }: AdminsTableProps) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const { admin: currentAdmin } = useAdmin()
  const canUpdateAdmins = hasPermission(currentAdmin, 'admins', 'update')
  const canDeleteAdmins = hasPermission(currentAdmin, 'admins', 'delete')
  const canResetAdmins = hasPermission(currentAdmin, 'admins', 'reset_usage')
  const canUseBulkSelection = canUpdateAdmins || canDeleteAdmins || canResetAdmins
  const [currentPage, setCurrentPage] = useState(0)
  const [itemsPerPage, setItemsPerPage] = useState(getAdminsPerPageLimitSize())
  const [isChangingPage, setIsChangingPage] = useState(false)
  const isFirstLoadRef = useRef(true)
  const [filters, setFilters] = useState<AdminFilters>({
    sort: '-created_at',
    limit: itemsPerPage,
    offset: 0,
  })
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false)
  const [statusToggleDialogOpen, setStatusToggleDialogOpen] = useState(false)
  const [resetUsersUsageDialogOpen, setResetUsersUsageDialogOpen] = useState(false)
  const [selectedAdminUsernames, setSelectedAdminUsernames] = useState<string[]>([])
  const [resetSelectionKey, setResetSelectionKey] = useState(0)
  const [bulkAction, setBulkAction] = useState<BulkAdminActionType | null>(null)
  const [adminToDelete, setAdminToDelete] = useState<AdminDetails | null>(null)
  const [adminToToggleStatus, setAdminToToggleStatus] = useState<AdminDetails | null>(null)
  const [adminToReset, setAdminToReset] = useState<AdminDetails | null>(null)
  const bulkDeleteAdminsMutation = useBulkDeleteAdmins()
  const bulkResetAdminsUsageMutation = useBulkResetAdminsUsage()
  const bulkDisableAdminsMutation = useBulkDisableAdmins()
  const bulkEnableAdminsMutation = useBulkEnableAdmins()

  const {
    data: adminsResponse,
    isLoading,
    isFetching,
    refetch,
  } = useGetAdmins(filters, {
    query: {
      staleTime: 0,
      gcTime: 0,
      retry: 1,
    },
  })

  const adminsData = adminsResponse?.admins || []
  const selectedAdmins = adminsData.filter(admin => selectedAdminUsernames.includes(admin.username))
  const selectedEnableEligibleAdmins = selectedAdmins.filter(admin => getAdminStatus(admin) === 'disabled')
  const selectedDisableEligibleAdmins = selectedAdmins.filter(admin => getAdminStatus(admin) !== 'disabled')
  const selectedAdminIds = compactAdminIds(selectedAdmins)
  const selectedEnableEligibleIds = compactAdminIds(selectedEnableEligibleAdmins)
  const selectedDisableEligibleIds = compactAdminIds(selectedDisableEligibleAdmins)

  // Expose counts to parent component for statistics
  useEffect(() => {
    if (onTotalAdminsChange) {
      if (adminsResponse) {
        onTotalAdminsChange({
          total: adminsResponse.total,
          active: adminsResponse.active,
          disabled: adminsResponse.disabled,
          limited: adminsResponse.limited,
        })
      } else {
        onTotalAdminsChange(null)
      }
    }
  }, [adminsResponse, onTotalAdminsChange])

  // Update filters when pagination changes
  useEffect(() => {
    setFilters(prev => ({
      ...prev,
      limit: itemsPerPage,
      offset: currentPage * itemsPerPage,
    }))
  }, [currentPage, itemsPerPage])

  useEffect(() => {
    if (adminsData && isFirstLoadRef.current) {
      isFirstLoadRef.current = false
    }
  }, [adminsData])

  useEffect(() => {
    if (!isFetching && isChangingPage) {
      setIsChangingPage(false)
    }
  }, [isFetching, isChangingPage])

  // When filters change (e.g., search), reset page if needed
  const handleFilterChange = (newFilters: Partial<AdminFilters>) => {
    setFilters(prev => {
      const resetPage = newFilters.username !== undefined && newFilters.username !== prev.username
      const updatedFilters = {
        ...prev,
        ...newFilters,
        offset: resetPage ? 0 : newFilters.offset !== undefined ? newFilters.offset : prev.offset,
      }
      // If username is explicitly set to undefined, remove it from the filters
      if ('username' in newFilters && newFilters.username === undefined) {
        delete updatedFilters.username
      }
      return updatedFilters
    })
    // Reset page if search changes
    if (newFilters.username !== undefined && newFilters.username !== filters.username) {
      setCurrentPage(0)
    }
  }

  const handleManualRefresh = async () => {
    return refetch()
  }

  const handleDeleteClick = (admin: AdminDetails) => {
    setAdminToDelete(admin)
    setDeleteDialogOpen(true)
  }

  const clearSelection = () => {
    setResetSelectionKey(prev => prev + 1)
    setSelectedAdminUsernames([])
  }

  const invalidateAdminQueries = useCallback(() => {
    queryClient.invalidateQueries({ queryKey: ['/api/admins'] })
    queryClient.invalidateQueries({ queryKey: ['waylane-product-subscriptions'] })
  }, [queryClient])

  const handleStatusToggleClick = (admin: AdminDetails) => {
    setAdminToToggleStatus(admin)
    setStatusToggleDialogOpen(true)
  }

  const handleResetUsersUsageClick = (admin: AdminDetails) => {
    setAdminToReset(admin)
    setResetUsersUsageDialogOpen(true)
  }
  const handleConfirmResetUsersUsage = async () => {
    if (adminToReset) {
      onResetUsage(adminToReset)
      setResetUsersUsageDialogOpen(false)
      setAdminToReset(null)
    }
  }

  const handleConfirmDelete = async () => {
    if (adminToDelete) {
      onDelete(adminToDelete)
      setDeleteDialogOpen(false)
      setAdminToDelete(null)
    }
  }

  const handleConfirmStatusToggle = async () => {
    if (adminToToggleStatus) {
      onToggleStatus(adminToToggleStatus)
      setStatusToggleDialogOpen(false)
      setAdminToToggleStatus(null)
    }
  }

  const handlePageChange = (newPage: number) => {
    if (newPage === currentPage || isChangingPage) return

    setIsChangingPage(true)
    setCurrentPage(newPage)
  }

  const handleItemsPerPageChange = (value: number) => {
    setIsChangingPage(true)
    setItemsPerPage(value)
    setCurrentPage(0) // Reset to first page when items per page changes
    setAdminsPerPageLimitSize(value.toString())
    setIsChangingPage(false)
  }

  const handleSort = (column: string, fromDropdown = false) => {
    const currentSort = filters.sort

    const cleanColumn = column.startsWith('-') ? column.slice(1) : column

    if (fromDropdown) {
      if (column.startsWith('-')) {
        if (currentSort === '-' + cleanColumn) {
          setFilters(prev => ({ ...prev, sort: '-created_at' }))
        } else {
          setFilters(prev => ({ ...prev, sort: '-' + cleanColumn }))
        }
      } else if (currentSort === cleanColumn) {
        setFilters(prev => ({ ...prev, sort: '-created_at' }))
      } else {
        setFilters(prev => ({ ...prev, sort: cleanColumn }))
      }
      return
    }

    if (currentSort === cleanColumn) {
      // First click: ascending, make it descending
      setFilters(prev => ({ ...prev, sort: '-' + cleanColumn }))
    } else if (currentSort === '-' + cleanColumn) {
      // Second click: descending, return to default sort
      setFilters(prev => ({ ...prev, sort: '-created_at' }))
    } else {
      // Default state or different column: make it ascending
      setFilters(prev => ({ ...prev, sort: cleanColumn }))
    }
  }

  const handleBulkDelete = async () => {
    if (!selectedAdminIds.length) return

    try {
      const response = await bulkDeleteAdminsMutation.mutateAsync({
        data: {
          ids: selectedAdminIds,
        },
      })
      toast.success(t('success', { defaultValue: 'Success' }), {
        description: t('admins.bulkDeleteSuccess', {
          count: response.count,
          defaultValue: '{{count}} admins deleted successfully.',
        }),
      })
      clearSelection()
      setBulkAction(null)
      invalidateAdminQueries()
    } catch (error: any) {
      toast.error(t('error', { defaultValue: 'Error' }), {
        description:
          error?.data?.detail ||
          error?.message ||
          t('admins.bulkDeleteFailed', {
            defaultValue: 'Failed to delete selected admins.',
          }),
      })
    }
  }

  const handleBulkResetUsage = async () => {
    if (!selectedAdminIds.length) return

    try {
      const response = await bulkResetAdminsUsageMutation.mutateAsync({
        data: {
          ids: selectedAdminIds,
        },
      })
      toast.success(t('success', { defaultValue: 'Success' }), {
        description: t('admins.bulkResetSuccess', {
          count: response.count,
          defaultValue: 'Usage reset for {{count}} admins.',
        }),
      })
      clearSelection()
      setBulkAction(null)
      invalidateAdminQueries()
    } catch (error: any) {
      toast.error(t('error', { defaultValue: 'Error' }), {
        description: error?.data?.detail || error?.message || t('admins.bulkResetFailed', { defaultValue: 'Failed to reset usage for selected admins.' }),
      })
    }
  }

  const handleBulkDisable = async () => {
    if (!selectedDisableEligibleIds.length) return

    try {
      const response = await bulkDisableAdminsMutation.mutateAsync({
        data: {
          ids: selectedDisableEligibleIds,
        },
      })
      toast.success(t('success', { defaultValue: 'Success' }), {
        description: t('admins.bulkDisableSuccess', {
          count: response.count,
          defaultValue: '{{count}} admins disabled successfully.',
        }),
      })
      clearSelection()
      setBulkAction(null)
      invalidateAdminQueries()
    } catch (error: any) {
      toast.error(t('error', { defaultValue: 'Error' }), {
        description: error?.data?.detail || error?.message || t('admins.bulkDisableFailed', { defaultValue: 'Failed to disable selected admins.' }),
      })
    }
  }

  const handleBulkEnable = async () => {
    if (!selectedEnableEligibleIds.length) return

    try {
      const response = await bulkEnableAdminsMutation.mutateAsync({
        data: {
          ids: selectedEnableEligibleIds,
        },
      })
      toast.success(t('success', { defaultValue: 'Success' }), {
        description: t('admins.bulkEnableSuccess', {
          count: response.count,
          defaultValue: '{{count}} admins enabled successfully.',
        }),
      })
      clearSelection()
      setBulkAction(null)
      invalidateAdminQueries()
    } catch (error: any) {
      toast.error(t('error', { defaultValue: 'Error' }), {
        description: error?.data?.detail || error?.message || t('admins.bulkEnableFailed', { defaultValue: 'Failed to enable selected admins.' }),
      })
    }
  }

  const selectedCount = selectedAdminUsernames.length
  const enableEligibleCount = selectedEnableEligibleAdmins.length
  const disableEligibleCount = selectedDisableEligibleAdmins.length
  const bulkActions: BulkActionItem[] = selectedCount
    ? [
        ...(canDeleteAdmins
          ? [
              {
                key: 'delete',
                label: t('delete'),
                icon: Trash2,
                onClick: () => setBulkAction('delete'),
                direct: true,
                destructive: true,
              } as BulkActionItem,
            ]
          : []),
        ...(canResetAdmins
          ? [
              {
                key: 'reset',
                label: t('admins.reset'),
                icon: RefreshCw,
                onClick: () => setBulkAction('reset'),
              } as BulkActionItem,
            ]
          : []),
        ...(canUpdateAdmins && disableEligibleCount > 0
          ? [
              {
                key: 'disable',
                label: t('disable'),
                icon: PowerOff,
                onClick: () => setBulkAction('disable'),
              } as BulkActionItem,
            ]
          : []),
        ...(canUpdateAdmins && enableEligibleCount > 0
          ? [
              {
                key: 'enable',
                label: t('enable'),
                icon: Power,
                onClick: () => setBulkAction('enable'),
              } as BulkActionItem,
            ]
          : []),
      ]
    : []
  const bulkActionConfigs: Record<BulkAdminActionType, BulkActionDialogConfig> = {
    delete: {
      title: t('admins.bulkDeleteTitle', { defaultValue: 'Delete Selected Admins' }),
      description: t('admins.bulkDeletePrompt', {
        count: selectedCount,
        defaultValue: 'Are you sure you want to delete {{count}} selected admins? This action cannot be undone.',
      }),
      actionLabel: t('delete'),
      onConfirm: handleBulkDelete,
      isPending: bulkDeleteAdminsMutation.isPending,
      destructive: true,
    },
    reset: {
      title: t('admins.bulkResetTitle', { defaultValue: 'Reset Usage for Selected Admins' }),
      description: t('admins.bulkResetPrompt', {
        count: selectedCount,
        defaultValue: 'Are you sure you want to reset usage for {{count}} selected admins?',
      }),
      actionLabel: t('admins.reset'),
      onConfirm: handleBulkResetUsage,
      isPending: bulkResetAdminsUsageMutation.isPending,
    },
    enable: {
      title: t('admins.bulkEnableTitle', { defaultValue: 'Enable Selected Admins' }),
      description: t('admins.bulkEnablePrompt', {
        count: enableEligibleCount,
        defaultValue: 'Are you sure you want to enable {{count}} selected admins?',
      }),
      actionLabel: t('enable'),
      onConfirm: handleBulkEnable,
      isPending: bulkEnableAdminsMutation.isPending,
    },
    disable: {
      title: t('admins.bulkDisableTitle', { defaultValue: 'Disable Selected Admins' }),
      description: t('admins.bulkDisablePrompt', {
        count: disableEligibleCount,
        defaultValue: 'Are you sure you want to disable {{count}} selected admins?',
      }),
      actionLabel: t('disable'),
      onConfirm: handleBulkDisable,
      isPending: bulkDisableAdminsMutation.isPending,
    },
  }
  const activeBulkActionConfig = bulkAction ? bulkActionConfigs[bulkAction] : null

  const columns = setupColumns({
    t,
    handleSort,
    filters,
    currentAdminUsername: currentAdmin?.username,
    onEdit: canUpdateAdmins ? onEdit : undefined,
    onDelete: canDeleteAdmins ? handleDeleteClick : undefined,
    toggleStatus: canUpdateAdmins ? handleStatusToggleClick : undefined,
    onResetUsage: canResetAdmins ? handleResetUsersUsageClick : undefined,
  })

  const isCurrentlyLoading = isLoading || (isFetching && !adminsResponse)
  const isPageLoading = isChangingPage || (isFetching && !isFirstLoadRef.current)

  return (
    <div>
      <Filters filters={filters} onFilterChange={handleFilterChange} handleSort={handleSort} refetch={handleManualRefresh} />
      {canUseBulkSelection && <BulkActionsBar selectedCount={selectedCount} onClear={clearSelection} actions={bulkActions} />}
      <DataTable
        columns={columns}
        data={adminsData || []}
        onEdit={canUpdateAdmins ? onEdit : undefined}
        onDelete={canDeleteAdmins ? handleDeleteClick : undefined}
        onToggleStatus={canUpdateAdmins ? handleStatusToggleClick : undefined}
        onResetUsage={canResetAdmins ? handleResetUsersUsageClick : undefined}
        onSelectionChange={canUseBulkSelection ? setSelectedAdminUsernames : undefined}
        resetSelectionKey={resetSelectionKey}
        currentAdminUsername={currentAdmin?.username}
        enableSelection={canUseBulkSelection}
        setStatusToggleDialogOpen={setStatusToggleDialogOpen}
        isLoading={isCurrentlyLoading && isFirstLoadRef.current}
        isFetching={isPageLoading}
      />
      <PaginationControls
        currentPage={currentPage}
        totalPages={Math.ceil((adminsResponse?.total || 0) / itemsPerPage)}
        itemsPerPage={itemsPerPage}
        totalItems={adminsResponse?.total || 0}
        isLoading={isPageLoading}
        onPageChange={handlePageChange}
        onItemsPerPageChange={handleItemsPerPageChange}
      />
      {adminToDelete && <DeleteAlertDialog admin={adminToDelete} isOpen={deleteDialogOpen} onClose={() => setDeleteDialogOpen(false)} onConfirm={handleConfirmDelete} />}
      {adminToToggleStatus && (
        <ToggleAdminStatusModal admin={adminToToggleStatus} isOpen={statusToggleDialogOpen} onClose={() => setStatusToggleDialogOpen(false)} onConfirm={handleConfirmStatusToggle} />
      )}
      {adminToReset && (
        <ResetUsersUsageConfirmationDialog
          adminUsername={adminToReset.username}
          onConfirm={handleConfirmResetUsersUsage}
          isOpen={resetUsersUsageDialogOpen}
          onClose={() => setResetUsersUsageDialogOpen(false)}
        />
      )}
      {activeBulkActionConfig && (
        <BulkActionAlertDialog
          open={!!bulkAction}
          onOpenChange={open => setBulkAction(open ? bulkAction : null)}
          title={activeBulkActionConfig.title}
          description={activeBulkActionConfig.description}
          actionLabel={activeBulkActionConfig.actionLabel}
          onConfirm={activeBulkActionConfig.onConfirm}
          isPending={activeBulkActionConfig.isPending}
          destructive={activeBulkActionConfig.destructive}
        />
      )}
    </div>
  )
}
