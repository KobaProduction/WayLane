import { Button } from '@/components/ui/button'

export type XrayUsageRow = {
  subscription_uuid: string
  total_uplink: string
  total_downlink: string
  epoch_started_at: string
  observed_at: string
}
export type XrayUsageState = {
  billing_authoritative: false
  billing_enabled: boolean
  source: string
  samples: XrayUsageRow[]
}

export function NodeXrayUsage({ data, busy, onRefresh }: { data?: XrayUsageState; busy: boolean; onRefresh: () => void }) {
  return <section className="space-y-3 border-t pt-3" aria-label="Статистика Xray по UUID подписок">
    <div className="flex flex-wrap items-center justify-between gap-2">
      <h3 className="text-sm font-semibold">Статистика Xray по UUID подписок</h3>
      <Button size="sm" variant="outline" disabled={busy} onClick={onRefresh}>Обновить счётчики</Button>
    </div>
    <p className="text-xs text-muted-foreground">
      Суммарные счётчики из StatsService/WSS — это наблюдение, не скорость соединения и не списанные коммерческие байты.
      Источник требует настройки Xray StatsService и явного разрешения чтения на Node.
    </p>
    <p className="text-xs text-muted-foreground">Коммерческий учёт: {data?.billing_enabled ? 'разрешён в Backend' : 'выключен (только диагностика)'}</p>
    {!data && <p className="text-xs text-muted-foreground">Данные ещё не загружены.</p>}
    {data?.samples.length === 0 && <p className="text-xs text-muted-foreground">Счётчики пока не поступали.</p>}
    <div className="space-y-2">
      {data?.samples.slice(0, 20).map(sample => <div key={sample.subscription_uuid} className="min-w-0 border-b pb-2 text-xs">
        <p className="break-all font-mono">{sample.subscription_uuid}</p>
        <p className="text-muted-foreground">
          ↑ {BigInt(sample.total_uplink).toLocaleString('ru-RU')} Б · ↓ {BigInt(sample.total_downlink).toLocaleString('ru-RU')} Б
          {' · '}наблюдение {new Date(sample.observed_at).toLocaleString()}
        </p>
      </div>)}
    </div>
    {(data?.samples.length ?? 0) > 20 && <p className="text-xs text-muted-foreground">Первые 20 записей из {data?.samples.length}.</p>}
  </section>
}
