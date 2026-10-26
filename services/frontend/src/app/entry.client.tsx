import App from '@/app/App'
import { installChunkLoadRecovery } from '@/utils/chunk-recovery'
import { installBrowserDiagnostics } from '@/features/telemetry/browser-diagnostics'
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'

installChunkLoadRecovery()
installBrowserDiagnostics()

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
