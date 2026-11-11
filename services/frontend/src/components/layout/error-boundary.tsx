import { ErrorPage } from '@/components/layout/error-page'
import { reportReactRenderFailure } from '@/features/telemetry/browser-diagnostics'
import { Component, type ErrorInfo, type ReactNode } from 'react'

type ErrorBoundaryProps = {
  children: ReactNode
}

type ErrorBoundaryState = {
  hasError: boolean
  error: unknown
  componentStack?: string
}

export class ErrorBoundary extends Component<ErrorBoundaryProps, ErrorBoundaryState> {
  state: ErrorBoundaryState = {
    hasError: false,
    error: undefined,
    componentStack: undefined,
  }

  static getDerivedStateFromError(error: unknown): ErrorBoundaryState {
    return { hasError: true, error }
  }

  componentDidCatch(_error: unknown, errorInfo: ErrorInfo) {
    reportReactRenderFailure()
    this.setState({ componentStack: errorInfo.componentStack || undefined })
  }

  reset = () => {
    this.setState({ hasError: false, error: undefined, componentStack: undefined })
  }

  render() {
    if (this.state.hasError) {
      return <ErrorPage error={this.state.error} componentStack={this.state.componentStack} resetError={this.reset} />
    }

    return this.props.children
  }
}
