import { useEffect, useState } from 'react'
import { Link } from 'react-router'
import { Gauge, RefreshCw } from 'lucide-react'
import { CoreEditorFormDialog } from '@/features/core-editor/components/shared/core-editor-form-dialog'
import { Button } from '@/components/ui/button'
import { $fetch } from '@/service/http'

type Scope = { mode: 'single'; outboundTag: string } | { mode: 'all' } | null
type Assignment = {
  node_uuid: string
  display_name: string
  registration_status: string
  runtime_status: string
  generation: number
  applied_generation: number
  outbound_tags: string[]
  latency_supported: false
}

export function OutboundLatencyTestDialog({
  open, onOpenChange, scope, coreId,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  scope: Scope
  coreId: number | null
}) {
  const [assignments, setAssignments] = useState<Assignment[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [updated, setUpdated] = useState(false)

  async function refresh() {
    if (!coreId || !open) return
    setLoading(true)
    setError('')
    try {
      const result = await $fetch<{ assignments: Assignment[] }>(
        '/api/node-registry/cores/' + coreId + '/assignments')
      setAssignments(result.assignments)
      setUpdated(true)
    } catch {
      setError('Не удалось получить состояние Core на зарегистрированных Node')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    if (!open || !coreId) {
      setAssignments([])
      setUpdated(false)
      return
    }
    void refresh()
  }, [open, coreId])

  return <CoreEditorFormDialog
    isDialogOpen={open} onOpenChange={onOpenChange}
    title="Состояние outbound на автономных Node"
    leadingIcon={<Gauge className="h-5 w-5" />}
    size="lg" inlinePersistValidation={false}
    footerExtra={<Button variant="outline" onClick={() => void refresh()} disabled={loading || !coreId}>
      <RefreshCw className="mr-2 h-4 w-4" /> Обновить
    </Button>}>
    <div className="space-y-3 text-sm">
      <p className="text-muted-foreground">
        Показывается фактически подтверждённая ревизия Xray и теги outbound из
        назначенного профиля. Это не измерение задержки соединения:
        штатный автономный WSS-контракт пока не предоставляет RTT конкретного outbound.
        Ранее использовавшийся числовой NodeService RPC удалён.
      </p>
      {scope?.mode === 'single' && <p className="font-mono">Outbound: {scope.outboundTag}</p>}
      {coreId == null && <p>Сохраните Core в редакторе, затем назначьте его Node.</p>}
      {loading && <p>Загрузка состояния Node…</p>}
      {error && <p role="alert" className="text-destructive">{error}</p>}
      {!loading && updated && assignments.length === 0 && <p>
        К этому сохранённому Core не назначено ни одной автономной Node.
      </p>}
      {assignments.map(node => (
        <div className="rounded-md border p-3" key={node.node_uuid}>
          <p className="font-medium">{node.display_name} · {node.runtime_status}</p>
          <p className="text-muted-foreground text-xs">
            Ревизия {node.applied_generation}/{node.generation} ·
            регистрация {node.registration_status}
          </p>
          <p className="text-muted-foreground mt-2 text-xs">
            {scope?.mode === 'single'
              ? node.outbound_tags.includes(scope.outboundTag)
                ? 'Outbound присутствует в назначенном профиле'
                : 'Outbound отсутствует в назначенном профиле'
              : node.outbound_tags.length
                ? 'Outbound: ' + node.outbound_tags.join(', ')
                : 'Нет outbound в сохранённой конфигурации'}
          </p>
          <Link to="/nodes" className="mt-2 inline-block text-xs underline"
            onClick={() => onOpenChange(false)}>Открыть Node Registry</Link>
        </div>
      ))}
    </div>
  </CoreEditorFormDialog>
}
