import type { UserOut, UserRole } from '@/generated/openapi'

export type Role = UserRole
export type User = UserOut

export interface Session {
  user: User
  expiresAt: string
}
