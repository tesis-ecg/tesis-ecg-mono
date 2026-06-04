import { api } from '@/lib/api'

import type { Session, User } from './types'

export async function loginRequest(email: string, password: string): Promise<Session> {
  const { data } = await api.post<Session>('/auth/login', { email, password })
  return data
}

export async function logoutRequest(): Promise<void> {
  await api.post('/auth/logout')
}

export async function meRequest(): Promise<User> {
  const { data } = await api.get<User>('/auth/me')
  return data
}
