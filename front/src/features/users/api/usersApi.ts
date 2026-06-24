import type { CreateUserInput, UpdateUserEmailInput, UserAccount } from '../types'
import { createMockUser, deleteMockUser, getMockUsers, updateMockUserEmail } from '../mocks'

export async function listUsers(): Promise<UserAccount[]> {
  // TODO(backend): GET /users (admin) — ver ticket "Gestión de usuarios".
  // Cuando el endpoint exista, reemplazar el mock por el call real:
  //   import { api } from '@/lib/api'
  //   const { data } = await api.get<UserAccount[]>('/users')
  //   return data
  return getMockUsers()
}

export async function createUser(input: CreateUserInput): Promise<UserAccount> {
  // TODO(backend): POST /users (admin) — crea en Auth0 + DB (+ perfil doctor si role=medico).
  //   const { data } = await api.post<UserAccount>('/users', input)
  //   return data
  return createMockUser(input)
}

export async function updateUserEmail(
  id: string,
  input: UpdateUserEmailInput,
): Promise<UserAccount> {
  // TODO(backend): PATCH /users/:id (admin) — actualiza email en Auth0 + DB.
  //   const { data } = await api.patch<UserAccount>(`/users/${id}`, input)
  //   return data
  return updateMockUserEmail(id, input)
}

export async function deleteUser(id: string): Promise<void> {
  // TODO(backend): DELETE /users/:id (admin) — soft-delete en DB + baja/bloqueo en Auth0.
  //   await api.delete(`/users/${id}`)
  deleteMockUser(id)
}

export async function sendPasswordReset(id: string): Promise<void> {
  // TODO(backend): POST /users/:id/password-reset (admin) — dispara el email de reseteo de Auth0.
  //   await api.post(`/users/${id}/password-reset`)
  void id
}
