export type Role = 'medico' | 'admin' | 'investigador' | 'asistente'

export interface User {
  id: string
  email: string
  fullName: string
  role: Role
}

export interface Session {
  user: User
  expiresAt: string
}
