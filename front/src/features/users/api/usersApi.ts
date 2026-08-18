import { api } from '@/lib/api'

import type { CreateUserInput, UpdateUserEmailInput, UserAccount } from '../types'

export async function listUsers(): Promise<UserAccount[]> {
  const { data } = await api.get<UserAccount[]>('/users')
  return data
}

export async function createUser(input: CreateUserInput): Promise<UserAccount> {
  const { data } = await api.post<UserAccount>('/users', input)
  return data
}

export async function updateUserEmail(
  id: string,
  input: UpdateUserEmailInput,
): Promise<UserAccount> {
  const { data } = await api.patch<UserAccount>(`/users/${id}`, input)
  return data
}

export async function deleteUser(id: string): Promise<void> {
  await api.delete(`/users/${id}`)
}

export async function sendPasswordReset(id: string): Promise<void> {
  await api.post(`/users/${id}/password-reset`)
}
