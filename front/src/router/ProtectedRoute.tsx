import { Loader2 } from 'lucide-react'
import { Navigate, Outlet, useLocation } from 'react-router-dom'

import { useAuth } from '@/features/auth/AuthContext'

/**
 * Envuelve rutas privadas. Si la sesión aún se está validando muestra un
 * spinner full-screen; si no hay sesión, redirige a `/login?from=<actual>`.
 */
export function ProtectedRoute() {
  const { isAuthenticated, loading } = useAuth()
  const location = useLocation()

  if (loading) {
    return (
      <div className="flex h-screen items-center justify-center bg-bg">
        <Loader2 className="animate-spin text-primary-500" size={32} />
      </div>
    )
  }

  if (!isAuthenticated) {
    const from = `${location.pathname}${location.search}`
    const params = new URLSearchParams({ from })
    return <Navigate to={`/login?${params.toString()}`} replace />
  }

  return <Outlet />
}
