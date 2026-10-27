import { useEffect, useState } from 'react'
import { $fetch } from '@/service/http'
import { useAdmin } from '@/hooks/use-admin'
import { hasPermission } from '@/utils/rbac'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Badge } from '@/components/ui/badge'

type Invitation = {
  id: number
  label: string
  revoked: '0' | '1'
  created_at: string
  expires_at: string | null
}

type Registration = {
  node_uuid: string
  display_name: string
  status: 'pending' | 'approved' | 'revoked'
  agent_version: string
  observed_ip: string | null
  reported_ipv4: string | null
  reported_ipv6: string | null
  capabilities: string[]
  last_seen_at: string
}

/** Optional v2 registry, separate from the existing legacy node cards. */
export default function NodeDiscoveryPanel() {
  const { admin } = useAdmin()
  const canRead = hasPermission(admin, 'nodes', 'read')
  const canCreate = hasPermission(admin, 'nodes', 'create')
  const canApprove = hasPermission(admin, 'nodes', 'update')
  const [entries, setEntries] = useState<Registration[]>([])
  const [invitations, setInvitations] = useState<Invitation[]>([])
  const [label, setLabel] = useState('')
  const [token, setToken] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const refresh = async () => {
    try {
      const [result, invites] = await Promise.all([
        $fetch<{ registrations: Registration[] }>('/api/node-discovery/registrations'),
        $fetch<{ invitations: Invitation[] }>('/api/node-discovery/invitations'),
      ])
      setEntries(result.registrations)
      setInvitations(invites.invitations)
      setError('')
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load discovered agents')
    }
  }

  useEffect(() => { if (canRead) void refresh() }, [canRead])

  const invite = async () => {
    if (!label.trim() || !canCreate) return
    setLoading(true)
    setToken('')
    try {
      const response = await $fetch<{ discovery_token: string }>('/api/node-discovery/invitations', {
        method: 'POST', body: { label: label.trim() },
      })
      setToken(response.discovery_token)
      setLabel('')
      await refresh()
      toast.success('Discovery token created')
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Invitation could not be created')
    } finally { setLoading(false) }
  }

  const revokeInvitation = async (id: number) => {
    if (!canApprove) return
    setLoading(true)
    try {
      await $fetch('/api/node-discovery/invitations/' + id + '/revoke', { method: 'POST' })
      await refresh()
      toast.success('Discovery invitation revoked')
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Failed to revoke invitation')
    } finally { setLoading(false) }
  }

  const decide = async (uuid: string, operation: 'approve' | 'revoke') => {
    if (!canApprove) return
    setLoading(true)
    try {
      await $fetch('/api/node-discovery/registrations/' + encodeURIComponent(uuid) + '/' + operation, { method: 'POST' })
      await refresh()
      toast.success(operation === 'approve' ? 'Agent approved' : 'Agent revoked')
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Failed to update agent')
    } finally { setLoading(false) }
  }

  if (!canRead) return null

  return (
    <Card className="mb-5">
      <CardHeader>
        <CardTitle>Node Discovery — подготовка</CardTitle>
        <CardDescription>
          Новый агент регистрируется по постоянному токену, но не получает права запускать VPN-ядра до подтверждения.
          Текущие ноды, сертификаты, адреса хостов и подписки не изменяются.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {canCreate && (
          <div className="flex flex-col gap-2 sm:flex-row">
            <Input
              className="min-w-0 flex-1"
              aria-label="Discovery invitation name"
              placeholder="Название токена для группы нод"
              value={label}
              onChange={e => setLabel(e.target.value)}
              maxLength={120}
            />
            <Button type="button" onClick={() => void invite()} disabled={loading || !label.trim()} className="w-full sm:w-auto">
              Создать токен
            </Button>
          </div>
        )}
        {token && (
          <div className="min-w-0 space-y-2 rounded-md border p-3">
            <p className="text-sm">Скопируйте токен сейчас. В панели хранится только его хеш; повторно показать значение нельзя.</p>
            <div className="flex min-w-0 flex-col gap-2 sm:flex-row sm:items-center">
              <Input aria-label="New discovery token" className="min-w-0 flex-1 font-mono text-xs" value={token} readOnly type="password" />
              <Button variant="outline" type="button" onClick={() => void navigator.clipboard.writeText(token)}>Копировать</Button>
              <Button variant="ghost" type="button" onClick={() => setToken('')}>Скрыть</Button>
            </div>
          </div>
        )}
        {invitations.length > 0 && (
          <div className="space-y-2">
            <p className="text-sm font-medium">Discovery-токены ({invitations.length})</p>
            {invitations.map(item => (
              <div key={item.id} className="flex min-w-0 flex-wrap items-center justify-between gap-2 rounded-md border p-3">
                <div className="min-w-0 space-y-1">
                  <span className="break-words text-sm">{item.label}</span>
                  <p className="text-muted-foreground text-xs">{item.revoked === '1' ? 'Отозван' : item.expires_at ? 'Ограничен по времени' : 'Постоянный'}</p>
                </div>
                {canApprove && item.revoked !== '1' && (
                  <Button type="button" size="sm" variant="outline" disabled={loading}
                    onClick={() => void revokeInvitation(item.id)}>Отозвать токен</Button>
                )}
              </div>
            ))}
          </div>
        )}
        <div className="flex items-center justify-between gap-2">
          <span className="text-sm font-medium">Заявки агентов ({entries.length})</span>
          <Button type="button" variant="outline" size="sm" onClick={() => void refresh()} disabled={loading}>Обновить</Button>
        </div>
        {error && <p role="alert" className="text-destructive text-sm">{error}</p>}
        {entries.length === 0 && !error && <p className="text-muted-foreground text-sm">Заявок пока нет.</p>}
        <div className="space-y-2">
          {entries.map(node => (
            <div key={node.node_uuid} className="flex min-w-0 flex-col justify-between gap-3 rounded-md border p-3 sm:flex-row sm:items-center">
              <div className="min-w-0 space-y-1">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="min-w-0 break-words text-sm font-medium">{node.display_name}</span>
                  <Badge variant="secondary">{node.status}</Badge>
                </div>
                <p className="text-muted-foreground break-all font-mono text-xs">{node.node_uuid}</p>
                <p className="text-muted-foreground break-words text-xs">
                  Agent {node.agent_version} · observed {node.observed_ip ?? 'unknown'} · reported {node.reported_ipv4 ?? node.reported_ipv6 ?? 'unknown'}
                </p>
              </div>
              {canApprove && node.status !== 'revoked' && (
                <div className="flex shrink-0 gap-2">
                  {node.status === 'pending' && <Button size="sm" disabled={loading} onClick={() => void decide(node.node_uuid, 'approve')}>Подтвердить</Button>}
                  <Button variant="outline" size="sm" disabled={loading} onClick={() => void decide(node.node_uuid, 'revoke')}>Отозвать</Button>
                </div>
              )}
            </div>
          ))}
        </div>
      </CardContent>
    </Card>
  )
}
