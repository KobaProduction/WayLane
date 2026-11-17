import PageHeader from '@/components/layout/page-header'
import { SubscriptionFirewallDesigner } from '@/features/subscriptions/components/subscription-firewall-designer'
import { useAdmin } from '@/hooks/use-admin'
import { hasPermission } from '@/utils/rbac'
import { Link } from 'react-router'
import { useTranslation } from 'react-i18next'

export default function SubscriptionFirewallPage() {
  const { admin } = useAdmin()
  const { t } = useTranslation()
  const canUpdate = hasPermission(admin, 'settings', 'update')
  if (!hasPermission(admin, 'settings', 'read')) {
    return <p role="alert" className="px-4 py-6 text-sm text-muted-foreground">{t('subscriptionFirewall.ui.page.noAccess')}</p>
  }

  return (
    <div className="flex min-w-0 flex-1 flex-col">
      <PageHeader title="subscriptionFirewall.title" description="subscriptionFirewall.description" />
      <div className="flex flex-wrap items-center justify-between gap-2 px-4 pt-3">
        <p className="text-sm text-muted-foreground">{t('subscriptionFirewall.ui.page.description')}</p>
        {canUpdate && <Link className="text-primary text-sm font-medium underline underline-offset-4" to="/settings/subscriptions">{t('subscriptionFirewall.ui.page.settingsLink')}</Link>}
      </div>
      <SubscriptionFirewallDesigner canUpdate={canUpdate} />
    </div>
  )
}
