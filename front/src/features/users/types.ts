import type { Role } from '@/features/auth/types'

/** Cuenta de usuario gestionable desde la tab Usuarios (solo admin). */
export interface UserAccount {
  id: string
  email: string
  fullName: string
  role: Role
  isActive: boolean
  createdAt: string
}

/** Roles que un admin puede asignar al crear un usuario. */
export type CreatableRole = Extract<Role, 'medico' | 'admin'>

export interface CreateUserInput {
  fullName: string
  email: string
  password: string
  role: CreatableRole
}

export interface UpdateUserEmailInput {
  email: string
}
