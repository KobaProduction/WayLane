import { UserCheck, UsersIcon } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { SystemSubscriptionsStats } from '@/service/api'

const SubscriptionStatisticsCard = ({ data }: { data: SystemSubscriptionsStats | undefined }) => {
  const { t } = useTranslation()
  const totalSubscriptions = data?.total_subscriptions ?? 0
  const activeSubscriptions = data?.active_subscriptions ?? 0
  const percentOfTotal = (value: number | undefined) => {
    if (!totalSubscriptions || value === undefined || value <= 0) return null
    return Math.round((value / totalSubscriptions) * 100)
  }
  const percentOfActive = (value: number | undefined) => {
    if (!activeSubscriptions || value === undefined || value <= 0) return null
    return Math.round((value / activeSubscriptions) * 100)
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="truncate">{t('statistics.subscriptions')}</CardTitle>
        <CardDescription className="truncate">{t('monitorSubscriptions')}</CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-2">
        <div className="flex min-w-0 flex-row items-center gap-2 rounded-lg border p-3 shadow-sm md:gap-3 md:p-4">
          <UsersIcon className="text-muted-foreground size-5 md:size-6" />
          <span className="truncate text-sm md:text-base">{t('statistics.subscriptions')}</span>
          <span className="ms-auto text-sm font-bold md:text-base">{totalSubscriptions}</span>
        </div>
        <div className="flex min-w-0 flex-row items-center gap-2 rounded-lg border p-3 shadow-sm md:gap-3 md:p-4">
          <UserCheck className="text-muted-foreground size-5 md:size-6" />
          <span className="truncate text-sm md:text-base">{t('statistics.activeSubscriptions')}</span>
          <div className="ms-auto flex items-center gap-2">
            {percentOfTotal(data?.active_subscriptions) !== null && (
              <Badge variant="secondary" className="text-[10px] md:text-xs">
                {percentOfTotal(data?.active_subscriptions)}%
              </Badge>
            )}
            <span className="text-sm font-bold md:text-base">{data?.active_subscriptions || 0}</span>
          </div>
        </div>
        <div className="flex min-w-0 flex-row items-center gap-2 rounded-lg border p-3 shadow-sm md:gap-3 md:p-4">
          <div className="size-2 rounded-full bg-green-600 md:size-3" />
          <span className="truncate text-sm md:text-base">{t('statistics.onlineSubscriptions')}</span>
          <div className="ms-auto flex items-center gap-2">
            {percentOfActive(data?.online_subscriptions) !== null && (
              <Badge variant="secondary" className="text-[10px] md:text-xs">
                {percentOfActive(data?.online_subscriptions)}%
              </Badge>
            )}
            <span className="text-sm font-bold md:text-base">{data?.online_subscriptions || 0}</span>
          </div>
        </div>
        <div className="flex min-w-0 flex-row items-center gap-2 rounded-lg border p-3 shadow-sm md:gap-3 md:p-4">
          <div className="size-2 rounded-full bg-orange-600 md:size-3" />
          <span className="truncate text-sm md:text-base">{t('statistics.expiredSubscriptions')}</span>
          <div className="ms-auto flex items-center gap-2">
            {percentOfTotal(data?.expired_subscriptions) !== null && (
              <Badge variant="secondary" className="text-[10px] md:text-xs">
                {percentOfTotal(data?.expired_subscriptions)}%
              </Badge>
            )}
            <span className="text-sm font-bold md:text-base">{data?.expired_subscriptions || 0}</span>
          </div>
        </div>
        <div className="flex min-w-0 flex-row items-center gap-2 rounded-lg border p-3 shadow-sm md:gap-3 md:p-4">
          <div className="size-2 rounded-full bg-red-600 md:size-3" />
          <span className="truncate text-sm md:text-base">{t('statistics.limitedSubscriptions')}</span>
          <div className="ms-auto flex items-center gap-2">
            {percentOfTotal(data?.limited_subscriptions) !== null && (
              <Badge variant="secondary" className="text-[10px] md:text-xs">
                {percentOfTotal(data?.limited_subscriptions)}%
              </Badge>
            )}
            <span className="text-sm font-bold md:text-base">{data?.limited_subscriptions || 0}</span>
          </div>
        </div>
        <div className="flex min-w-0 flex-row items-center gap-2 rounded-lg border p-3 shadow-sm md:gap-3 md:p-4">
          <div className="size-2 rounded-full bg-purple-600 md:size-3" />
          <span className="truncate text-sm md:text-base">{t('statistics.onHoldSubscriptions')}</span>
          <div className="ms-auto flex items-center gap-2">
            {percentOfTotal(data?.on_hold_subscriptions) !== null && (
              <Badge variant="secondary" className="text-[10px] md:text-xs">
                {percentOfTotal(data?.on_hold_subscriptions)}%
              </Badge>
            )}
            <span className="text-sm font-bold md:text-base">{data?.on_hold_subscriptions || 0}</span>
          </div>
        </div>
        <div className="flex min-w-0 flex-row items-center gap-2 rounded-lg border p-3 shadow-sm md:gap-3 md:p-4">
          <div className="size-2 rounded-full bg-slate-600 md:size-3" />
          <span className="truncate text-sm md:text-base">{t('statistics.disabledSubscriptions')}</span>
          <div className="ms-auto flex items-center gap-2">
            {percentOfTotal(data?.disabled_subscriptions) !== null && (
              <Badge variant="secondary" className="text-[10px] md:text-xs">
                {percentOfTotal(data?.disabled_subscriptions)}%
              </Badge>
            )}
            <span className="text-sm font-bold md:text-base">{data?.disabled_subscriptions || 0}</span>
          </div>
        </div>
      </CardContent>
    </Card>
  )
}

export default SubscriptionStatisticsCard
