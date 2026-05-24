import { Navigate, Outlet } from 'react-router-dom'

import { useAuth } from '@/features/auth/AuthContext'
import type { Role } from '@/features/auth/types'

interface RoleRouteProps {
  /** Roles autorizados (cualquiera basta). Admin siempre pasa. */
  allow: Role[]
}

/**
 * Filtra rutas según el rol del usuario. Asume que ya hay sesión (anidar
 * dentro de `<ProtectedRoute>`).
 */
export function RoleRoute({ allow }: RoleRouteProps) {
  const { user } = useAuth()

  if (!user) {
    return <Navigate to="/login" replace />
  }

  if (user.role !== 'admin' && !allow.includes(user.role)) {
    return <Navigate to="/403" replace />
  }

  return <Outlet />
}
