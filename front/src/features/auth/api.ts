// import { api } from '@/lib/api'
import { createApiError } from '@/lib/apiError'
import { mockDelay } from '@/lib/mockDelay'

import { loadSession } from './storage'
import type { Session, User } from './types'

/**
 * MOCK ACTIVO — backend pendiente (ver TES-16).
 * Para activar el endpoint real cuando esté disponible:
 *   1. Descomentar la línea `api.post(...)` y el `return data`.
 *   2. Borrar todo el bloque marcado `// MOCK`.
 *   3. Borrar el import de `mockDelay`.
 */
export async function loginRequest(email: string, password: string): Promise<Session> {
  // TODO(TES-16 backend): descomentar cuando el endpoint exista
  // const { data } = await api.post<Session>('/auth/login', { email, password })
  // return data

  // MOCK ↓
  await mockDelay()
  if (!email.includes('@') || password.length < 6) {
    throw createApiError({
      status: 401,
      code: 'UNAUTHORIZED',
      message: 'Credenciales inválidas. Verificá email y contraseña.',
    })
  }
  const mockUser: User = {
    id: 'mock-user-1',
    email,
    fullName: 'Dr. Tomás Serra',
    role: 'medico',
  }
  const expiresAt = new Date(Date.now() + 8 * 60 * 60 * 1000).toISOString()
  return {
    token: 'mock-jwt-token-' + Math.random().toString(36).slice(2),
    user: mockUser,
    expiresAt,
  }
  // MOCK ↑
}

export async function logoutRequest(): Promise<void> {
  // TODO(TES-16 backend): descomentar cuando el endpoint exista
  // await api.post('/auth/logout')

  // MOCK ↓
  await mockDelay(150)
  // MOCK ↑
}

export async function meRequest(): Promise<User> {
  // TODO(TES-16 backend): descomentar cuando el endpoint exista
  // const { data } = await api.get<User>('/auth/me')
  // return data

  // MOCK ↓ — devuelve el user de la sesión persistida; si no hay, 401.
  await mockDelay(150)
  const session = loadSession()
  if (!session) {
    throw createApiError({
      status: 401,
      code: 'UNAUTHORIZED',
      message: 'Sesión expirada.',
    })
  }
  return session.user
  // MOCK ↑
}
