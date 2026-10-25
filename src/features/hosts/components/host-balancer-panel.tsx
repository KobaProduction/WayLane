import { useCallback, useEffect, useState } from 'react'
import { toast } from 'sonner'
import { $fetch } from '@/service/http'
import type { BaseHost } from '@/service/api'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Checkbox } from '@/components/ui/checkbox'

type Strategy = 'roundRobin' | 'random' | 'leastPing' | 'leastLoad'
type Balancer = {
  id: number
  name: string
  host_ids: number[]
  strategy: Strategy
  probe_interval: number
  hide_members: boolean
  is_disabled: boolean
}
const endpoint = '/api/host-balancers'
const errorMessage = (error: unknown) => error instanceof Error ? error.message : 'Ошибка запроса'

export default function HostBalancerPanel({ hosts, canCreate, canUpdate }: {
  hosts: BaseHost[]
  canCreate: boolean
  canUpdate: boolean
}) {
  const [items, setItems] = useState<Balancer[]>([])
  const [editingId, setEditingId] = useState<number | null>(null)
  const [name, setName] = useState('')
  const [memberIds, setMemberIds] = useState<number[]>([])
  const [strategy, setStrategy] = useState<Strategy>('roundRobin')
  const [probeInterval, setProbeInterval] = useState(60)
  const [hideMembers, setHideMembers] = useState(true)
  const [disabled, setDisabled] = useState(false)
  const [busy, setBusy] = useState(false)

  const refresh = useCallback(async () => {
    setItems(await $fetch<Balancer[]>(endpoint))
  }, [])

  useEffect(() => {
    void refresh().catch(error => toast.error(errorMessage(error)))
  }, [refresh])

  function reset() {
    setEditingId(null)
    setName('')
    setMemberIds([])
    setStrategy('roundRobin')
    setProbeInterval(60)
    setHideMembers(true)
    setDisabled(false)
  }

  function edit(item: Balancer) {
    setEditingId(item.id)
    setName(item.name)
    setMemberIds([...item.host_ids])
    setStrategy(item.strategy)
    setProbeInterval(item.probe_interval)
    setHideMembers(item.hide_members)
    setDisabled(item.is_disabled)
  }

  function toggleMember(id: number, selected: boolean) {
    setMemberIds(current => selected ? [...current, id] : current.filter(item => item !== id))
  }

  async function save() {
    if (!name.trim() || memberIds.length < 2) {
      toast.error('Укажите название и минимум два хоста.')
      return
    }
    setBusy(true)
    try {
      await $fetch(editingId === null ? endpoint : endpoint + '/' + editingId, {
        method: editingId === null ? 'POST' : 'PUT',
        body: { name: name.trim(), host_ids: memberIds, strategy, probe_interval: probeInterval,
          hide_members: hideMembers, is_disabled: disabled },
      })
      await refresh()
      reset()
      toast.success('Балансировщик сохранён.')
    } catch (error) {
      toast.error(errorMessage(error))
    } finally {
      setBusy(false)
    }
  }

  async function remove(item: Balancer) {
    if (!window.confirm('Удалить балансировщик «' + item.name + '»? Сами хосты сохранятся.')) return
    setBusy(true)
    try {
      await $fetch(endpoint + '/' + item.id, { method: 'DELETE' })
      await refresh()
      if (editingId === item.id) reset()
      toast.success('Балансировщик удалён.')
    } catch (error) {
      toast.error(errorMessage(error))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Балансировка хостов Xray</CardTitle>
        <p className="text-sm text-muted-foreground">
          Один клиентский профиль из нескольких существующих хостов. Поддерживается Xray JSON, без вложенных групп.
        </p>
      </CardHeader>
      <CardContent className="space-y-4">
        {items.map(item => (
          <div key={item.id} className="flex flex-wrap items-center justify-between gap-2 rounded-md border p-3">
            <div>
              <p className="font-medium">{item.name}{item.is_disabled ? ' (отключён)' : ''}</p>
              <p className="text-xs text-muted-foreground">
                {item.host_ids.length} хоста · {item.strategy}{item.hide_members ? ' · участники скрыты в Xray' : ''}
              </p>
            </div>
            {canUpdate && <div className="flex gap-2">
              <Button size="sm" variant="outline" onClick={() => edit(item)}>Изменить</Button>
              <Button size="sm" variant="destructive" disabled={busy} onClick={() => void remove(item)}>Удалить</Button>
            </div>}
          </div>
        ))}

        {(canCreate || canUpdate) && (
          <div className="space-y-3 rounded-md border p-4">
            <div className="flex items-center justify-between">
              <h3 className="font-medium">{editingId === null ? 'Новая группа' : 'Редактирование группы'}</h3>
              {editingId !== null && <Button size="sm" variant="outline" onClick={reset}>Отмена</Button>}
            </div>
            <Input aria-label="Название балансировщика" placeholder="Например, Европа — автоматический выбор"
              value={name} onChange={event => setName(event.target.value)} maxLength={120} />
            <div>
              <label htmlFor="host-balancer-strategy" className="mb-1 block text-sm">Алгоритм</label>
              <select id="host-balancer-strategy" value={strategy}
                onChange={event => setStrategy(event.target.value as Strategy)}
                className="bg-background h-10 w-full rounded-md border px-3 text-sm">
                <option value="roundRobin">По очереди (round-robin)</option>
                <option value="random">Случайный (random)</option>
                <option value="leastPing">Минимальный пинг (observatory)</option>
                <option value="leastLoad">Наиболее стабильный (burstObservatory)</option>
              </select>
            </div>
            {(strategy === 'leastPing' || strategy === 'leastLoad') && (
              <div>
                <label htmlFor="host-balancer-interval" className="mb-1 block text-sm">Интервал проверки, секунд (30–3600)</label>
                <Input id="host-balancer-interval" type="number" min={30} max={3600} value={probeInterval}
                  onChange={event => setProbeInterval(Number(event.target.value))} />
              </div>
            )}
            <div>
              <p className="mb-2 text-sm font-medium">Хосты-участники (минимум два)</p>
              <div className="grid max-h-52 gap-2 overflow-y-auto sm:grid-cols-2">
                {hosts.filter(host => host.id != null).map(host => (
                  <label key={host.id} className="flex items-center gap-2 rounded-md border p-2 text-sm">
                    <Checkbox checked={memberIds.includes(host.id!)}
                      onCheckedChange={checked => toggleMember(host.id!, checked === true)} />
                    <span>{host.remark || 'Хост #' + host.id}{host.is_disabled ? ' (отключён)' : ''}</span>
                  </label>
                ))}
              </div>
            </div>
            <label className="flex items-center gap-2 text-sm">
              <Checkbox checked={hideMembers} onCheckedChange={checked => setHideMembers(checked === true)} />
              Скрыть отдельные хосты из Xray-подписки
            </label>
            <label className="flex items-center gap-2 text-sm">
              <Checkbox checked={disabled} onCheckedChange={checked => setDisabled(checked === true)} />
              Отключить балансировщик
            </label>
            <Button disabled={busy || !(editingId === null ? canCreate : canUpdate) || memberIds.length < 2}
              onClick={() => void save()}>
              {editingId === null ? 'Создать балансировщик' : 'Сохранить изменения'}
            </Button>
          </div>
        )}
      </CardContent>
    </Card>
  )
}
