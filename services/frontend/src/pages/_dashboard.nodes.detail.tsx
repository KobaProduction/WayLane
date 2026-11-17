import NodeRegistryPanel from '@/features/nodes/components/node-registry-panel'
import { useParams } from 'react-router'

export default function NodeDetailPage() {
  const { nodeUuid } = useParams()
  return <NodeRegistryPanel key={nodeUuid} detailNodeUuid={nodeUuid ?? ''} />
}
