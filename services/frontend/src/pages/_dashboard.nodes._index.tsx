import Nodes from '@/features/nodes/components/nodes-list'
import NodeDiscoveryPanel from '@/features/nodes/components/node-discovery-panel'

export default function NodesPage() {
  return (
    <>
      <NodeDiscoveryPanel />
      <Nodes />
    </>
  )
}
