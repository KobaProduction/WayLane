import { Badge } from '@/components/ui/badge'

export type NodeReleaseOperation = {
  operation_id: string | null
  generation: number
  action: 'update' | 'reconnect' | null
  image: string | null
  previous_image: string | null
  status: 'idle' | 'pending' | 'queued' | 'running' | 'succeeded' | 'failed' | 'rolled_back'
  phase: string
  progress: number
  error_code: string | null
  updated_at?: string | null
}

const statuses: Record<NodeReleaseOperation['status'], string> = {
  idle: 'Операций пока нет',
  pending: 'Ожидает подтверждения',
  queued: 'В очереди Agent',
  running: 'Выполняется',
  succeeded: 'Agent сообщил об успехе',
  failed: 'Операция завершилась с ошибкой',
  rolled_back: 'Agent сообщил об откате',
}

export function NodeReleaseStatus({ operation }: { operation?: NodeReleaseOperation }) {
  if (!operation) return <p className="text-xs text-muted-foreground">Состояние операции ещё не загружено.</p>
  if (operation.status === 'idle') return <p className="text-xs text-muted-foreground">Операции пока не выполнялись.</p>
  const active = ['pending', 'queued', 'running'].includes(operation.status)
  const variant = operation.status === 'failed' ? 'red' : operation.status === 'succeeded' ? 'green' : 'outline'
  return <div className="space-y-2 rounded-md border p-3 text-xs" aria-live="polite">
    <div className="flex flex-wrap items-center gap-2">
      <Badge variant={variant}>{statuses[operation.status]}</Badge>
      <span className="text-muted-foreground">{operation.action === 'update' ? 'Обновление Node' : operation.action === 'reconnect' ? 'WSS переподключение' : 'Операция Node'}</span>
    </div>
    <p role="status" className="text-muted-foreground">Этап: {operation.phase || '—'} · поколение {operation.generation}</p>
    {active && <>
      <progress aria-label="Этапный прогресс операции Node" className="w-full" max={100} value={operation.progress} />
      <p className="text-muted-foreground">{operation.progress}% — доля завершённых этапов, не объём скачивания.</p>
    </>}
    {operation.image && <p className="break-all font-mono text-muted-foreground">Цель: {operation.image}</p>}
    {operation.previous_image && <p className="break-all font-mono text-muted-foreground">Предыдущий образ: {operation.previous_image}</p>}
    {operation.updated_at && <p className="text-muted-foreground">Обновлено: {new Date(operation.updated_at).toLocaleString()}</p>}
    {operation.error_code && <p role="alert" className="text-destructive">Код ошибки: {operation.error_code}</p>}
  </div>
}
