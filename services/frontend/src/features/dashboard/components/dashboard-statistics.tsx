import { Users, UserCheck, Wifi, Download, Upload } from 'lucide-react'
import { Card, CardContent } from '@/components/ui/card'
import type { SystemUsersStats } from '@/service/api'
import { formatBytes } from '@/utils/formatByte'
import { useTranslation } from 'react-i18next'

/** Operational/business summary only. Host hardware monitoring lives in Grafana. */
export default function DashboardStatistics({ usersData }: { usersData: SystemUsersStats | undefined }) {
  const { t } = useTranslation()
  const total = Number(usersData?.total_user ?? 0)
  const active = Number(usersData?.active_users ?? 0)
  const online = Number(usersData?.online_users ?? 0)
  const upload = Number(usersData?.incoming_bandwidth ?? 0)
  const download = Number(usersData?.outgoing_bandwidth ?? 0)
  const values = [
    { label: t('dashboardOverview.totalUsers'), value: total.toLocaleString(), Icon: Users },
    { label: t('dashboardOverview.active'), value: active.toLocaleString(), Icon: UserCheck },
    { label: t('dashboardOverview.online'), value: online.toLocaleString(), Icon: Wifi },
    { label: t('dashboardOverview.upload'), value: formatBytes(upload), Icon: Upload },
    { label: t('dashboardOverview.download'), value: formatBytes(download), Icon: Download },
  ]
  return <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
    {values.map(({ label, value, Icon }) => <Card key={label}>
      <CardContent className="flex items-center gap-3 p-4">
        <Icon size={21} className="shrink-0 text-primary" />
        <div className="min-w-0">
          <p className="text-xs text-muted-foreground">{label}</p>
          <p className="truncate text-xl font-semibold tabular-nums">{usersData ? value : '—'}</p>
        </div>
      </CardContent>
    </Card>)}
  </div>
}
