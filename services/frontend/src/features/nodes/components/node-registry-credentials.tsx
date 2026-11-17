import type { Dispatch, SetStateAction } from 'react'
import { Link } from 'react-router'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { OFFICIAL_NODE_REPOSITORY } from './node-release-image'

export type NodeRegistryProfile = {
  repository: string
  username: string
  has_credentials: boolean
  credential_scope?: 'node' | 'shared' | 'none'
  official: boolean
  is_default: boolean
}

export type RegistryDraft = { repository: string; username: string; password: string }

type Props = {
  profiles: NodeRegistryProfile[] | undefined
  draft: RegistryDraft
  setDraft: Dispatch<SetStateAction<RegistryDraft>>
  busy: boolean
  canUpdate: boolean
  onSave: (draft: RegistryDraft) => void
  onSetDefault: (repository: string) => void
  onDelete: (repository: string) => void
}

/** Per-Node registry settings. Password fields are write-only; the API returns only credential presence. */
export function NodeRegistryCredentials({ profiles, draft, setDraft, busy, canUpdate, onSave, onSetDefault, onDelete }: Props) {
  const resolved = profiles ?? [{
    repository: OFFICIAL_NODE_REPOSITORY, username: '', has_credentials: false,
    official: true, is_default: true,
  }]
  return (
    <section className="space-y-4 rounded-md border p-3" aria-label="Registry этой Node">
      <div className="space-y-1">
        <h3 className="text-sm font-semibold">Registry этой Node</h3>
        <p className="text-xs text-muted-foreground">Доступ к образам для одной Node. Пароли сохраняются на сервере и не отображаются повторно.</p>
      </div>
      <div className="space-y-2">
        {resolved.map(profile => <div key={profile.repository} className="flex min-w-0 flex-wrap items-center gap-2 rounded-md border p-3 text-xs">
          <span className="min-w-0 flex-1 break-all font-mono">{profile.repository}</span>
          <span className="text-muted-foreground">{profile.has_credentials
            ? profile.credential_scope === 'shared' ? 'Общий GHCR доступ' : 'Авторизация настроена'
            : 'Без авторизации'}</span>
          {profile.is_default && <Badge variant="secondary">Основной</Badge>}
          {canUpdate && !profile.is_default && <Button type="button" size="sm" variant="outline" disabled={busy}
            onClick={() => onSetDefault(profile.repository)}>Основной</Button>}
          {profile.official && <Link className="text-primary underline underline-offset-2" to="/nodes/tokens">Общий GHCR токен</Link>}
          {canUpdate && !profile.official && <Button type="button" size="sm" variant="outline"
            onClick={() => setDraft({ repository: profile.repository, username: profile.username, password: '' })}>Изменить</Button>}
          {canUpdate && (!profile.official || profile.credential_scope === 'node') && <Button type="button" size="sm"
            variant="ghost" disabled={busy} onClick={() => onDelete(profile.repository)}>
            {profile.official ? 'Сбросить локальный доступ' : 'Удалить'}
          </Button>}
        </div>)}
      </div>
      {canUpdate && <div className="space-y-2 border-t pt-3">
        <h4 className="text-sm font-medium">{draft.repository ? 'Настроить Registry' : 'Добавить Registry'}</h4>
        <Input aria-label="Репозиторий Registry этой Node" placeholder="registry.example.com/waylane/node"
          value={draft.repository} maxLength={55}
          onChange={event => setDraft(previous => ({ ...previous, repository: event.target.value }))} />
        <div className="grid gap-2 sm:grid-cols-2">
          <Input aria-label="Логин Registry этой Node" placeholder="Имя пользователя (необязательно)"
            value={draft.username} maxLength={128} autoComplete="off"
            onChange={event => setDraft(previous => ({ ...previous, username: event.target.value }))} />
          <Input aria-label="Пароль Registry этой Node" type="password" autoComplete="new-password"
            placeholder="Пароль (пусто — сохранить прежний)" value={draft.password} maxLength={1024}
            onChange={event => setDraft(previous => ({ ...previous, password: event.target.value }))} />
        </div>
        <Button type="button" size="sm" disabled={busy || !draft.repository.trim() || draft.repository.trim() === OFFICIAL_NODE_REPOSITORY}
          onClick={() => onSave(draft)}>Сохранить Registry</Button>
      </div>}
    </section>
  )
}
