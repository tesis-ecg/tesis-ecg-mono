/**
 * Datos mock — borrar este archivo y eliminar imports en `api/usersApi.ts`
 * cuando los endpoints reales del backend estén disponibles (gestión de
 * usuarios, ver tickets de backend).
 *
 * Usa un store mutable a nivel de módulo para que crear/editar/eliminar se
 * reflejen en la lista durante la sesión (sin persistencia real).
 */
import type { CreateUserInput, UpdateUserEmailInput, UserAccount } from './types'

function daysAgoISO(days: number): string {
  return new Date(Date.now() - days * 24 * 60 * 60 * 1000).toISOString()
}

let store: UserAccount[] = [
  {
    id: 'u_admin',
    email: 'admin@holter.ar',
    fullName: 'Admin Root',
    role: 'admin',
    isActive: true,
    createdAt: daysAgoISO(180),
  },
  {
    id: 'u_001',
    email: 'maria.gomez@holter.ar',
    fullName: 'María Gómez',
    role: 'medico',
    isActive: true,
    createdAt: daysAgoISO(120),
  },
  {
    id: 'u_002',
    email: 'juan.perez@holter.ar',
    fullName: 'Juan Pérez',
    role: 'medico',
    isActive: true,
    createdAt: daysAgoISO(80),
  },
  {
    id: 'u_003',
    email: 'lucia.fernandez@holter.ar',
    fullName: 'Lucía Fernández',
    role: 'investigador',
    isActive: true,
    createdAt: daysAgoISO(45),
  },
]

let counter = 100

export function getMockUsers(): UserAccount[] {
  return [...store].sort((a, b) => a.fullName.localeCompare(b.fullName))
}

export function createMockUser(input: CreateUserInput): UserAccount {
  const user: UserAccount = {
    id: `u_${counter++}`,
    email: input.email,
    fullName: input.fullName,
    role: input.role,
    isActive: true,
    createdAt: new Date().toISOString(),
  }
  store = [user, ...store]
  return user
}

export function updateMockUserEmail(id: string, input: UpdateUserEmailInput): UserAccount {
  store = store.map((u) => (u.id === id ? { ...u, email: input.email } : u))
  const updated = store.find((u) => u.id === id)
  if (!updated) throw new Error('Usuario no encontrado')
  return updated
}

export function deleteMockUser(id: string): void {
  store = store.filter((u) => u.id !== id)
}
