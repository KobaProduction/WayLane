import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'
import { AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle, AlertDialogTrigger } from '@/components/ui/alert-dialog'

export type XrayCommand = 'deploy' | 'update' | 'restart' | 'stop' | 'reconfigure'

type Props = {
  imageVersion: string
  region: string
  configuration: string
  configured: boolean
  busy: boolean
  onImageVersionChange: (value: string) => void
  onRegionChange: (value: string) => void
  onConfigurationChange: (value: string) => void
  onGenerateSample: () => void
  onCommand: (action: XrayCommand) => void
  onRefresh: () => void
}

function ConfirmXrayChange({ label, title, explanation, disabled, action }: {
  label: string; title: string; explanation: string; disabled: boolean; action: () => void
}) {
  return <AlertDialog>
    <AlertDialogTrigger asChild>
      <Button type="button" size="sm" variant="outline" disabled={disabled}>{label}</Button>
    </AlertDialogTrigger>
    <AlertDialogContent>
      <AlertDialogHeader>
        <AlertDialogTitle>{title}</AlertDialogTitle>
        <AlertDialogDescription>{explanation}</AlertDialogDescription>
      </AlertDialogHeader>
      <AlertDialogFooter>
        <AlertDialogCancel>Отмена</AlertDialogCancel>
        <AlertDialogAction onClick={action}>Подтвердить операцию</AlertDialogAction>
      </AlertDialogFooter>
    </AlertDialogContent>
  </AlertDialog>
}

/** Advanced actions are deliberately collapsed; the saved Core preset is the default workflow. */
export function NodeXrayRuntimeControls({ imageVersion, region, configuration, configured, busy,
  onImageVersionChange, onRegionChange, onConfigurationChange, onGenerateSample, onCommand, onRefresh }: Props) {
  return <details className="rounded-md border p-3" data-testid="node-xray-advanced-controls">
    <summary className="cursor-pointer text-sm font-medium">Ручные операции Xray и JSON</summary>
    <div className="mt-3 space-y-3">
      <p className="text-xs text-muted-foreground">Для стандартной настройки используйте сохранённое ядро выше. Применение ручного JSON или перезапуск может прервать VPN-подключения.</p>
      <div className="grid gap-2 sm:grid-cols-2">
        <Input aria-label="Xray image version" value={imageVersion}
          onChange={event => onImageVersionChange(event.target.value)} placeholder="Версия Xray, например 26.10.21" />
        <Input aria-label="Xray region label" value={region} maxLength={64}
          onChange={event => onRegionChange(event.target.value)} placeholder="Метка региона" />
      </div>
      <Textarea aria-label="Xray configuration JSON" value={configuration} rows={8} className="font-mono text-xs"
        onChange={event => onConfigurationChange(event.target.value)} placeholder="inbounds / outbounds / routing" />
      <div className="flex flex-wrap gap-2">
        <Button variant="outline" size="sm" type="button" onClick={onGenerateSample}>Создать пример SOCKS5</Button>
        <ConfirmXrayChange label="Развернуть JSON" title="Развернуть ручную конфигурацию Xray?"
          explanation="Ручной JSON может заменить текущий Xray Core и прервать VPN-подключения. Сохранённая конфигурация профиля будет затронута."
          disabled={busy || !configuration.trim()} action={() => onCommand('deploy')} />
        <ConfirmXrayChange label="Обновить Xray и перезапустить" title="Обновить образ Xray?"
          explanation="Обновление образа перезапустит Xray, активные VPN-подключения могут прерваться."
          disabled={busy} action={() => onCommand('update')} />
        <ConfirmXrayChange label="Применить JSON без рестарта" title="Применить новую конфигурацию Xray?"
          explanation="Даже без перезапуска новая конфигурация может изменить маршруты и прервать VPN-подключения."
          disabled={busy || !configuration.trim() || !configured} action={() => onCommand('reconfigure')} />
        <AlertDialog>
          <AlertDialogTrigger asChild>
            <Button variant="outline" size="sm" type="button" disabled={busy || !configured}>Перезапустить Xray</Button>
          </AlertDialogTrigger>
          <AlertDialogContent>
            <AlertDialogHeader>
              <AlertDialogTitle>Перезапустить Xray на этой Node?</AlertDialogTitle>
              <AlertDialogDescription>Текущие VPN-подключения через это ядро будут прерваны. Другие Node не затрагиваются.</AlertDialogDescription>
            </AlertDialogHeader>
            <AlertDialogFooter>
              <AlertDialogCancel>Отмена</AlertDialogCancel>
              <AlertDialogAction onClick={() => onCommand('restart')}>Перезапустить</AlertDialogAction>
            </AlertDialogFooter>
          </AlertDialogContent>
        </AlertDialog>
        <AlertDialog>
          <AlertDialogTrigger asChild>
            <Button variant="destructive" size="sm" type="button" disabled={busy || !configured}>Остановить Xray</Button>
          </AlertDialogTrigger>
          <AlertDialogContent>
            <AlertDialogHeader>
              <AlertDialogTitle>Остановить Xray на этой Node?</AlertDialogTitle>
              <AlertDialogDescription>VPN-подключения через это ядро будут прерваны. Действие не затрагивает другие Node и ядра.</AlertDialogDescription>
            </AlertDialogHeader>
            <AlertDialogFooter>
              <AlertDialogCancel>Отмена</AlertDialogCancel>
              <AlertDialogAction onClick={() => onCommand('stop')}>Остановить Xray</AlertDialogAction>
            </AlertDialogFooter>
          </AlertDialogContent>
        </AlertDialog>
        <Button type="button" size="sm" variant="ghost" disabled={busy} onClick={onRefresh}>Обновить статус</Button>
      </div>
    </div>
  </details>
}
