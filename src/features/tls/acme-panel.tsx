import { useCallback, useEffect, useState } from 'react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { $fetch } from '@/service/http'

type DNSAccount = {
  id: string
  name: string
  provider: string
}

type ACMEOrder = {
  id: string
  name: string
  preset_id: string | null
  dns_account_id: string
  domains: string[]
  environment: string
  status: string
  auto_renew: boolean
  last_error: string | null
  next_renewal_at: string | null
}

const api = '/api/acme'
const message = (error: unknown) => error instanceof Error ? error.message : 'Ошибка запроса'

export function ACMEPanel() {
  const [accounts, setAccounts] = useState<DNSAccount[]>([])
  const [orders, setOrders] = useState<ACMEOrder[]>([])
  const [accountName, setAccountName] = useState('')
  const [dnsToken, setDnsToken] = useState('')
  const [zoneToken, setZoneToken] = useState('')
  const [orderName, setOrderName] = useState('')
  const [domainList, setDomainList] = useState('')
  const [email, setEmail] = useState('')
  const [accountId, setAccountId] = useState('')
  const [environment, setEnvironment] = useState<'staging' | 'production'>('staging')
  const [autoRenew, setAutoRenew] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')

  const refresh = useCallback(async () => {
    const [dns, jobs] = await Promise.all([
      $fetch<DNSAccount[]>(api + '/dns-accounts'),
      $fetch<ACMEOrder[]>(api + '/orders'),
    ])
    setAccounts(dns)
    setOrders(jobs)
  }, [])

  useEffect(() => {
    void refresh().catch(e => setError(message(e)))
  }, [refresh])

  async function execute(action: () => Promise<void>) {
    setBusy(true)
    setError('')
    setNotice('')
    try {
      await action()
      await refresh()
    } catch (e) {
      setError(message(e))
    } finally {
      setBusy(false)
    }
  }

  function createAccount() {
    void execute(async () => {
      if (!accountName.trim() || !dnsToken.trim()) throw new Error('Укажите название и Cloudflare API-токен.')
      await $fetch(api + '/dns-accounts', {
        method: 'POST',
        body: {
          name: accountName.trim(),
          dns_token: dnsToken.trim(),
          zone_token: zoneToken.trim() || null,
        },
      })
      setDnsToken('')
      setZoneToken('')
      setAccountName('')
      setNotice('DNS-аккаунт создан. API-токены зашифрованы и больше не отображаются.')
    })
  }

  function createOrder() {
    void execute(async () => {
      const domains = domainList.split(/[\s,]+/).filter(Boolean)
      if (!orderName.trim() || !email.trim() || !accountId || !domains.length) {
        throw new Error('Укажите название, email, Cloudflare-аккаунт и домены.')
      }
      const result = await $fetch<{ id: string }>(api + '/orders', {
        method: 'POST',
        body: {
          name: orderName.trim(),
          dns_account_id: accountId,
          domains,
          email: email.trim(),
          environment,
          auto_renew: autoRenew,
        },
      })
      setOrderName('')
      setDomainList('')
      setNotice('Заявка ' + result.id + ' создана. Нажмите «Обновить статусы» для проверки выдачи.')
    })
  }

  function renew(orderId: string) {
    void execute(async () => {
      await $fetch(api + '/orders/' + orderId + '/renew', { method: 'POST' })
      setNotice('Запрос на выпуск/обновление сертификата отправлен.')
    })
  }

  function toggleRenew(order: ACMEOrder) {
    void execute(async () => {
      await $fetch(api + '/orders/' + order.id, {
        method: 'PATCH', body: { auto_renew: !order.auto_renew },
      })
      setNotice('Настройка продления сохранена.')
    })
  }

  function renewDue() {
    void execute(async () => {
      const result = await $fetch<{ queued: number }>(api + '/renew-due', { method: 'POST' })
      setNotice('Запланировано заявок: ' + result.queued)
    })
  }

  return (
    <section className="bg-card space-y-6 rounded-xl border p-5">
      <div>
        <h2 className="text-lg font-semibold">ACME / Let's Encrypt</h2>
        <p className="text-muted-foreground mt-1 text-sm">
          Автоматическая выдача и продление сертификатов через DNS-01 Cloudflare.
          Основано на подходе OpenFlare с lego; новые сертификаты создаются в каталоге TLS-пресетов
          и могут назначаться на доступные ноды.
        </p>
      </div>
      {error && <div role="alert" className="rounded-md border border-destructive p-3 text-sm text-destructive">{error}</div>}
      {notice && <div role="status" className="rounded-md border p-3 text-sm">{notice}</div>}
      <div className="space-y-3 rounded-lg border p-4">
        <h3 className="font-semibold">1. DNS-учётная запись Cloudflare</h3>
        <p className="text-muted-foreground text-xs">
          Рекомендуется API-токен с доступом Zone:DNS:Edit и Zone:Zone:Read только к нужной зоне.
          При необходимости для Zone:Read укажите отдельный токен.
        </p>
        <div className="grid gap-3 sm:grid-cols-2">
          <label className="space-y-1 text-sm">
            <span>Название DNS-аккаунта</span>
            <Input placeholder="Cloudflare main" value={accountName} onChange={e => setAccountName(e.target.value)} />
          </label>
          <label className="space-y-1 text-sm">
            <span>DNS API Token</span>
            <Input type="password" autoComplete="off" value={dnsToken} onChange={e => setDnsToken(e.target.value)} />
          </label>
          <label className="space-y-1 text-sm sm:col-span-2">
            <span>Zone Read Token (необязательно)</span>
            <Input type="password" autoComplete="off" value={zoneToken} onChange={e => setZoneToken(e.target.value)} />
          </label>
        </div>
        <Button disabled={busy || !accountName.trim() || !dnsToken.trim()} onClick={createAccount}>
          Добавить DNS-аккаунт
        </Button>
      </div>
      <div className="space-y-3 rounded-lg border p-4">
        <h3 className="font-semibold">2. Выпуск сертификата</h3>
        <div className="grid gap-3 sm:grid-cols-2">
          <label className="space-y-1 text-sm">
            <span>Название сертификата</span>
            <Input placeholder="example.com wildcard" value={orderName} onChange={e => setOrderName(e.target.value)} />
          </label>
          <label className="space-y-1 text-sm">
            <span>ACME email</span>
            <Input type="email" placeholder="certs@example.com" value={email} onChange={e => setEmail(e.target.value)} />
          </label>
          <label className="space-y-1 text-sm sm:col-span-2">
            <span>Домены (через запятую или пробел)</span>
            <Input placeholder="example.com, *.example.com" value={domainList} onChange={e => setDomainList(e.target.value)} />
          </label>
          <label className="space-y-1 text-sm">
            <span>DNS-аккаунт</span>
            <select className="bg-background w-full rounded-md border px-3 py-2" value={accountId} onChange={e => setAccountId(e.target.value)}>
              <option value="">Выбрать</option>
              {accounts.map(account => <option key={account.id} value={account.id}>{account.name} ({account.provider})</option>)}
            </select>
          </label>
          <label className="space-y-1 text-sm">
            <span>Let's Encrypt</span>
            <select className="bg-background w-full rounded-md border px-3 py-2" value={environment}
              onChange={e => setEnvironment(e.target.value as 'staging' | 'production')}>
              <option value="staging">Staging — тестирование</option>
              <option value="production">Production — доверенный сертификат</option>
            </select>
          </label>
        </div>
        <label className="flex items-center gap-2 text-sm">
          <input type="checkbox" checked={autoRenew} onChange={e => setAutoRenew(e.target.checked)} />
          Продлевать при наступлении срока (за 7 дней, при запуске задания)
        </label>
        <Button disabled={busy || !accountId} onClick={createOrder}>Запросить сертификат</Button>
        <p className="text-muted-foreground text-xs">
          Выпуск выполняется на сервере в отдельной задаче. Для регулярного продления нужно
          запланировать одноразовую команду <code>waylane acme-renew-due</code> во внешнем оркестраторе.
        </p>
      </div>
      <div className="space-y-3 rounded-lg border p-4">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h3 className="font-semibold">3. ACME-заявки и продление</h3>
          <div className="flex flex-wrap gap-2">
            <Button variant="outline" size="sm" disabled={busy} onClick={() => void execute(refresh)}>Обновить статусы</Button>
            <Button variant="outline" size="sm" disabled={busy} onClick={renewDue}>Продлить истекающие</Button>
          </div>
        </div>
        {!orders.length && <p className="text-muted-foreground text-sm">Заявок пока нет.</p>}
        {orders.map(order => (
          <div key={order.id} className="space-y-2 rounded-md border p-3 text-sm">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <strong>{order.name}</strong>
              <span>{order.status} · {order.environment}</span>
            </div>
            <div className="break-all text-muted-foreground">{order.domains.join(', ')}</div>
            {order.preset_id && <div className="text-muted-foreground">TLS-пресет: <code>{order.preset_id}</code></div>}
            {order.next_renewal_at && <div className="text-muted-foreground">
              Следующее продление: {new Date(order.next_renewal_at).toLocaleString()}
            </div>}
            {order.last_error && <p className="text-destructive">{order.last_error}</p>}
            <div className="flex flex-wrap gap-2">
              <Button size="sm" variant="outline" disabled={busy || order.status === 'running'} onClick={() => renew(order.id)}>
                {order.preset_id ? 'Запросить продление' : 'Повторить выпуск'}
              </Button>
              <Button size="sm" variant="outline" disabled={busy} onClick={() => toggleRenew(order)}>
                {order.auto_renew ? 'Отключить автопродление' : 'Включить автопродление'}
              </Button>
            </div>
          </div>
        ))}
      </div>
    </section>
  )
}
