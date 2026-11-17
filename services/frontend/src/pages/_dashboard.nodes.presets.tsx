import { CorePresetsPanel } from '@/features/nodes/components/core-presets-panel'
import { useAdmin } from '@/hooks/use-admin'
import { hasPermission } from '@/utils/rbac'

export default function XrayPresetsPage() {
  const { admin } = useAdmin()
  const canRead = hasPermission(admin, 'cores', 'read')
  if (!canRead) return <p role="alert" className="px-4 py-6 text-sm text-muted-foreground">Нет доступа к пресетам Xray.</p>

  return (
    <div className="w-full space-y-4 px-4 py-4">
      <CorePresetsPanel
        canCreate={hasPermission(admin, 'cores', 'create')}
        canUpdate={hasPermission(admin, 'cores', 'update')}
        canDelete={hasPermission(admin, 'cores', 'delete')}
      />
    </div>
  )
}
