import { Component, type ErrorInfo, type ReactNode } from 'react'

import { Button } from '@/components/ui/button'

interface AppErrorBoundaryState {
  hasError: boolean
}

export class AppErrorBoundary extends Component<{ children: ReactNode }, AppErrorBoundaryState> {
  state: AppErrorBoundaryState = { hasError: false }

  static getDerivedStateFromError(): AppErrorBoundaryState {
    return { hasError: true }
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    if (import.meta.env.DEV) console.error('Unhandled React error', error, info)
  }

  render() {
    if (!this.state.hasError) return this.props.children

    return (
      <main className="flex min-h-screen items-center justify-center bg-gray-50 px-6">
        <section className="max-w-md rounded-lg border border-gray-200 bg-white p-6 text-center shadow-sm">
          <h1 className="text-h5 text-gray-900">No pudimos mostrar esta pantalla</h1>
          <p className="mt-2 text-body2 text-gray-600">
            Tus datos no se modificaron. Recargá la aplicación para volver a intentarlo.
          </p>
          <Button className="mt-5" onClick={() => window.location.reload()}>
            Recargar
          </Button>
        </section>
      </main>
    )
  }
}
