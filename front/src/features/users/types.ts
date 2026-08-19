import type { Role } from '@/features/auth/types'
import type { UserAccountOut, UserCreateRequest, UserUpdateEmailRequest } from '@/generated/openapi'

/** Cuenta de usuario gestionable desde la tab Usuarios (solo admin). */
export type UserAccount = UserAccountOut

/** Roles que un admin puede asignar al crear un usuario. */
export type CreatableRole = Extract<Role, 'medico' | 'admin'>

export type CreateUserInput = Omit<UserCreateRequest, 'role'> & { role: CreatableRole }

export type UpdateUserEmailInput = UserUpdateEmailRequest
