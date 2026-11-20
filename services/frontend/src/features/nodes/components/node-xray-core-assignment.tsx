import { Link } from 'react-router'
import { Button } from '@/components/ui/button'
import { AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle, AlertDialogTrigger } from '@/components/ui/alert-dialog'
import type { EditorCoreOption, XrayStatus } from './node-xray-types'

type Props = {
  cores: EditorCoreOption[]
  selectedCoreId: string
  onCoreChange: (id: string) => void
  current: XrayStatus | undefined
  busy: boolean
  onApply: () => void
  onApplyRouting: () => void
}

/** Saved Core is primary; no managed secret is transmitted to the browser by this selector. */
export function NodeXrayCoreAssignment({ cores, selectedCoreId, onCoreChange, current, busy, onApply, onApplyRouting }: Props) {
  const selected = cores.find(core => String(core.id) === selectedCoreId)
  const applied = cores.find(core => core.id === current?.source_core_id)
  const validSelection = Boolean(selectedCoreId && selected)
  const currentGeneration = current?.generation ?? 0
  return <section className="space-y-3" aria-label="Сохранённый Xray Core">
    <div className="space-y-1">
      <h3 className="text-sm font-semibold">Конфигурация Xray</h3>
    </div>
    {current?.source_core_id != null && <p className="text-xs text-muted-foreground">
      Выбран для Node: <Link className="text-primary underline" to={`/nodes/cores/${current.source_core_id}`}>
        {applied?.name ?? `Core #${current.source_core_id}`}
      </Link>
    </p>}
    <div className="flex flex-wrap items-center gap-2">
      <select aria-label="Сохранённый профиль Xray" className="border-input bg-background min-h-10 min-w-0 max-w-full flex-1 rounded-md border px-3 text-sm"
        value={selectedCoreId} onChange={event => onCoreChange(event.target.value)}>
        <option value="">Выберите Xray Core</option>
        {cores.map(core => <option key={core.id} value={core.id}>{core.name}</option>)}
      </select>
      {validSelection && <Link className="text-sm text-primary underline" to={`/nodes/cores/${selectedCoreId}`}>Открыть редактор</Link>}
      <AlertDialog>
        <AlertDialogTrigger asChild>
          <Button size="sm" disabled={busy || !current || !validSelection}>Применить профиль</Button>
        </AlertDialogTrigger>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Применить Xray Core к Node?</AlertDialogTitle>
            <AlertDialogDescription>Конфигурация Xray может быть заменена, а VPN-подключения прерваны. Другие Node не затрагиваются.</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Отмена</AlertDialogCancel>
            <AlertDialogAction onClick={onApply}>Применить конфигурацию</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
    <details className="rounded-md border p-3">
      <summary className="cursor-pointer text-sm font-medium">Только маршрутизация и балансировщики</summary>
      <div className="mt-3 space-y-2">
        <p className="text-xs text-muted-foreground">Применяются только routing rules и balancers выбранного Core, без замены других разделов Xray.</p>
        <Button type="button" variant="outline" size="sm" disabled={busy || !current?.configured || !validSelection || !currentGeneration}
          onClick={onApplyRouting}>Применить маршрутизацию</Button>
      </div>
    </details>
  </section>
}
