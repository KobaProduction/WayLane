import { Database, ShieldCheck } from 'lucide-react'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Link } from 'react-router'

export default function CleanupSettings() {
  return <div className="space-y-6 p-4 sm:py-6">
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Database className="size-5" /> Хранение данных и обслуживание
        </CardTitle>
        <CardDescription>
          Автономные Node используют UUID, WSS и накопительные счётчики Xray.
          Исторические числовые таблицы PasarGuard сохраняются исключительно
          для чтения и миграционной совместимости.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4 text-sm">
        <p>
          Прежняя кнопка очистки Node usage удалена вместе с NodeService RPC.
          Стирание новых курсоров Xray могло бы привести к повторному учёту
          трафика, поэтому они не удаляются через административную панель.
        </p>
        <p>
          Сроки хранения и очистка исторических записей будут настраиваться
          отдельно от коммерческих сумм и границ доступа подписок.
        </p>
        <Link className="inline-flex items-center gap-2 underline" to="/nodes">
          <ShieldCheck className="size-4" /> Открыть Node Registry
        </Link>
      </CardContent>
    </Card>
  </div>
}
