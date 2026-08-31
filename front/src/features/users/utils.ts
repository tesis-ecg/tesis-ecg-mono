import type { Role } from '@/features/auth/types'

export const ROLE_LABEL: Record<Role, string> = {
  medico: 'Médico',
  admin: 'Administrador',
  // No se puede crear ni listar desde `/users` — el backend deja los pacientes
  // fuera de esa pantalla porque sus cuentas se gestionan desde su ficha. La
  // etiqueta existe igual: `Role` la incluye y una fila sin label sería un
  // `undefined` en pantalla si algún día se lista en otro lado.
  paciente: 'Paciente',
}
