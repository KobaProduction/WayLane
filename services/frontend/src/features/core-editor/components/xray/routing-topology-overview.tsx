import { useMemo } from 'react'
import { useTranslation } from 'react-i18next'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { useCoreEditorStore } from '@/features/core-editor/state/core-editor-store'
import { balancersMissingObservation, inspectRoutingTopology } from '@/features/core-editor/kit/balancer-routing-topology'
import type { Profile } from '@waylane/xray-config-kit'

type Props = { profile: Profile; section: 'routing' | 'balancers' }

/** Static tag-reference preview. It never sends probes or changes the loaded Core. */
export function RoutingTopologyOverview({ profile, section }: Props) {
  const { t } = useTranslation()
  const setActiveSection = useCoreEditorStore(state => state.setActiveSection)
  const topology = useMemo(() => inspectRoutingTopology(profile), [profile])
  const missingRoutes = topology.rules.filter(rule => rule.targetMissing)
  const ignoredBalancers = topology.rules.filter(rule => rule.balancerIgnored)
  const brokenBalancers = topology.balancers.filter(balancer => balancer.unmatchedSelectors.length > 0 || balancer.fallbackMissing)
  const missingLinks = missingRoutes.length + brokenBalancers.length
  return <section className="min-w-0 space-y-3 rounded-lg border p-3 sm:p-4" aria-label={t('coreEditor.topology.title', { defaultValue: 'Связи маршрутизации' })}>
    <div className="flex flex-wrap items-start justify-between gap-2">
      <div className="space-y-1">
        <h3 className="text-sm font-semibold">{t('coreEditor.topology.title', { defaultValue: 'Связи маршрутизации' })}</h3>
        <p className="text-xs text-muted-foreground">{t('coreEditor.topology.description', { defaultValue: 'Проверка настроенных тегов, а не измерение доступности или фактического маршрута трафика.' })}</p>
      </div>
      <div className="flex flex-wrap gap-2">
        <Button type="button" size="sm" variant="outline" onClick={() => setActiveSection('outbounds')}>
          {t('coreEditor.topology.openOutbounds', { defaultValue: 'Исходящие' })}
        </Button>
        <Button type="button" size="sm" variant="outline" onClick={() => setActiveSection(section === 'routing' ? 'balancers' : 'routing')}>
          {section === 'routing' ? t('coreEditor.topology.openBalancers', { defaultValue: 'Балансировщики' })
            : t('coreEditor.topology.openRules', { defaultValue: 'Правила' })}
        </Button>
      </div>
    </div>
    <div className="flex flex-wrap items-center gap-2 text-xs" aria-label={t('coreEditor.topology.counts', { defaultValue: 'Связанные сущности' })}>
      <Badge variant="outline">{t('coreEditor.topology.outbounds', { defaultValue: 'Outbound' })}: {topology.outboundTags.length}</Badge>
      <Badge variant="outline">{t('coreEditor.topology.balancers', { defaultValue: 'Балансировщики' })}: {topology.balancers.length}</Badge>
      <Badge variant="outline">{t('coreEditor.topology.rules', { defaultValue: 'Правила' })}: {topology.rules.length}</Badge>
      {missingLinks > 0 && <Badge variant="outline">{t('coreEditor.topology.warnings', { defaultValue: 'Неоднозначные ссылки' })}: {missingLinks}</Badge>}
    </div>
    {balancersMissingObservation(profile) && <p role="status" className="break-words rounded-md border border-dashed px-3 py-2 text-xs text-muted-foreground">
      {t('coreEditor.topology.noObservation', { defaultValue: 'Для leastPing/leastLoad не задан источник проверок. Настройте Observatory через кнопку наблюдения; сам просмотр профиля настройки не меняет.' })}
    </p>}
    {section === 'balancers' && topology.balancers.length > 0 ? (
      <details className="min-w-0" open={brokenBalancers.length > 0 ? true : undefined}>
        <summary className="cursor-pointer rounded-md text-xs font-medium text-primary focus-visible:outline-2 focus-visible:outline-ring">
          {t('coreEditor.topology.showTagMatching', { defaultValue: 'Просмотреть соответствия outbound и использование правилами' })}
        </summary>
        <div className="mt-2 space-y-2" role="list" aria-label={t('coreEditor.topology.previewList', { defaultValue: 'Соответствие тегов балансировщиков' })}>
        {topology.balancers.map((balancer, index) => <div role="listitem" key={`${index}:${balancer.tag}`} className="min-w-0 rounded-md bg-muted/30 px-3 py-2 text-xs">
          <div className="flex flex-wrap items-center gap-2">
            <strong className="min-w-0 break-all text-foreground">{balancer.tag}</strong>
            <span className="text-muted-foreground">{t('coreEditor.topology.usedBy', { defaultValue: 'Правил использует' })}: {balancer.referencingRules.length}</span>
          </div>
          <p className="mt-1 break-words text-muted-foreground">{t('coreEditor.topology.matchingOutbounds', { defaultValue: 'Совпадающие outbound по префиксу' })}: {balancer.matchingOutboundTags.join(', ') || '—'}</p>
          {balancer.unmatchedSelectors.length > 0 && <p role="status" className="mt-1 break-all text-destructive">{t('coreEditor.topology.unmatchedSelectors', { defaultValue: 'Нет подходящих outbound для префиксов' })}: {balancer.unmatchedSelectors.join(', ')}</p>}
          {balancer.fallbackMissing && <p role="status" className="mt-1 break-all text-destructive">{t('coreEditor.topology.unknownFallback', { defaultValue: 'Fallback не существует' })}: {balancer.fallbackTag}</p>}
        </div>)}
        </div>
      </details>
    ) : section === 'routing' ? (
      <div className="space-y-2 text-xs" role="list" aria-label={t('coreEditor.topology.routingDiagnostics', { defaultValue: 'Проверка целей правил' })}>
        {missingRoutes.map(rule => <p key={rule.index} role="listitem" className="break-all text-destructive">
          {t('coreEditor.topology.rule', { defaultValue: 'Правило' })} #{rule.index + 1}: {t('coreEditor.topology.missingTarget', { defaultValue: 'Цель отсутствует' })} ({rule.outboundTag ?? rule.balancerTag ?? '—'})
        </p>)}
        {ignoredBalancers.map(rule => <p key={`ignored-${rule.index}`} role="listitem" className="break-all text-muted-foreground">
          {t('coreEditor.topology.rule', { defaultValue: 'Правило' })} #{rule.index + 1}: {t('coreEditor.topology.outboundWins', { defaultValue: 'Задан outboundTag; balancerTag игнорируется Xray.' })}
        </p>)}
        {missingRoutes.length === 0 && ignoredBalancers.length === 0 && <p role="listitem" className="text-muted-foreground">{t('coreEditor.topology.noDanglingRules', { defaultValue: 'Отсутствующих целей Routing не найдено.' })}</p>}
      </div>
    ) : null}
  </section>
}
