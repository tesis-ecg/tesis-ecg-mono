import type { Role } from '@/features/auth/types'

export const ROLE_LABEL: Record<Role, string> = {
  medico: 'Médico',
  admin: 'Administrador',
  investigador: 'Investigador',
  asistente: 'Asistente',
}
