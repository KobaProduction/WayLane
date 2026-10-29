import Nodes from '@/features/nodes/components/nodes-list'
import NodeDiscoveryPanel from '@/features/nodes/components/node-registry-panel'

export default function NodesPage() {
  return (
    <div className="space-y-6">
      <NodeDiscoveryPanel />
      <section aria-label="Legacy VPN-ноды" className="space-y-2">
        <div>
          <h2 className="text-lg font-semibold">Legacy-ноды PasarGuard</h2>
          <p className="text-muted-foreground text-sm">
            Ранее зарегистрированные VPN-ноды. Они остаются отдельными от управляемых и Discovery-нод
            до проверенной миграции ядер, сертификатов и трафика.
          </p>
        </div>
        <Nodes />
      </section>
    </div>
  )
}
