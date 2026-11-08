import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { AlertTriangle, Database, Server } from 'lucide-react'
import { toast } from 'sonner'
import { DatePicker } from '@/components/common/date-picker'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle, AlertDialogTrigger } from '@/components/ui/alert-dialog'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { useAdmin } from '@/hooks/use-admin'
import { useClearUsageData, UsageTable } from '@/service/api'
import { hasPermission } from '@/utils/rbac'
import { formatDateByLocale } from '@/utils/datePickerUtils'

export default function CleanupSettings() {
  const { t, i18n } = useTranslation()
  const { admin } = useAdmin()
  const [selectedTable, setSelectedTable] = useState<UsageTable | ''>('')
  const [start, setStart] = useState<Date | undefined>()
  const [end, setEnd] = useState<Date | undefined>()
  const mutation = useClearUsageData()
  const canClearNodeHistory = hasPermission(admin, 'nodes', 'delete')
  const formatDate = (date: Date) => formatDateByLocale(date, i18n.language === 'fa', false)

  const clearHistory = () => {
    if (!selectedTable || !canClearNodeHistory) return
    if (start && end && start >= end) {
      toast.error(t('settings.cleanup.clearUsageData.selectDateRange'))
      return
    }
    mutation.mutate(
      {
        table: selectedTable,
        params: {
          start: start?.toISOString(),
          end: end?.toISOString(),
        },
      },
      {
        onSuccess: () => toast.success(t('settings.cleanup.clearUsageData.clearSuccess', { table: selectedTable })),
        onError: () => toast.error(t('settings.cleanup.clearUsageData.clearFailed')),
      },
    )
  }

  return (
    <div className="space-y-6 p-4 sm:space-y-8 sm:py-6">
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2"><Database className="size-5" />{t('settings.cleanup.title', { defaultValue: 'Maintenance' })}</CardTitle>
          <CardDescription>{t('subscriptionCoreOwnedMaintenance', { defaultValue: 'Subscription expiration, commercial quotas and removal are managed by VPN Core, not by WayLane cleanup jobs.' })}</CardDescription>
        </CardHeader>
      </Card>
      {canClearNodeHistory ? (
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2"><Server className="size-5" />{t('settings.cleanup.clearUsageData.title')}</CardTitle>
            <CardDescription>{t('settings.cleanup.clearUsageData.description')}</CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <Select value={selectedTable} onValueChange={value => setSelectedTable(value as UsageTable)}>
              <SelectTrigger><SelectValue placeholder={t('settings.cleanup.clearUsageData.selectTablePlaceholder')} /></SelectTrigger>
              <SelectContent>
                <SelectItem value={UsageTable.node_subscription_usages}>{t('settings.cleanup.clearUsageData.tables.nodeUserUsages')}</SelectItem>
                <SelectItem value={UsageTable.node_usages}>{t('settings.cleanup.clearUsageData.tables.nodeUsages')}</SelectItem>
              </SelectContent>
            </Select>
            <div className="grid gap-3 sm:grid-cols-2">
              <DatePicker mode="single" date={start} onDateChange={setStart} label={t('settings.cleanup.clearUsageData.dataAfter')} placeholder={t('settings.cleanup.clearUsageData.dataAfterPlaceholder')} formatDate={formatDate} />
              <DatePicker mode="single" date={end} onDateChange={setEnd} label={t('settings.cleanup.clearUsageData.dataBefore')} placeholder={t('settings.cleanup.clearUsageData.dataBeforePlaceholder')} formatDate={formatDate} />
            </div>
            <Alert>
              <AlertTriangle className="size-4" />
              <AlertDescription>{t('settings.cleanup.clearUsageData.warning')}</AlertDescription>
            </Alert>
            <AlertDialog>
              <AlertDialogTrigger asChild>
                <Button type="button" variant="destructive" disabled={!selectedTable || mutation.isPending}>
                  {t('settings.cleanup.clearUsageData.clearData')}
                </Button>
              </AlertDialogTrigger>
              <AlertDialogContent>
                <AlertDialogHeader>
                  <AlertDialogTitle>{t('settings.cleanup.clearUsageData.confirmClear')}</AlertDialogTitle>
                  <AlertDialogDescription>{t('settings.cleanup.clearUsageData.confirmClearMessage', { table: selectedTable })}</AlertDialogDescription>
                </AlertDialogHeader>
                <AlertDialogFooter>
                  <AlertDialogCancel>{t('cancel')}</AlertDialogCancel>
                  <AlertDialogAction onClick={clearHistory} disabled={mutation.isPending}>{t('settings.cleanup.clearUsageData.clearData')}</AlertDialogAction>
                </AlertDialogFooter>
              </AlertDialogContent>
            </AlertDialog>
          </CardContent>
        </Card>
      ) : null}
    </div>
  )
}
