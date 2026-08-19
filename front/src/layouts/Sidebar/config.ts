import { Home, Users, HeartPulse, Activity, UserCog } from 'lucide-react'
import type { LucideIcon } from 'lucide-react'

import type { Role } from '@/features/auth/types'

export type NavItemConfig = {
  name: string
  path: string
  icon: LucideIcon
  /** Si se define, el ítem solo se muestra a estos roles (admin siempre ve todo). */
  roles?: Role[]
}

export const navItems: NavItemConfig[] = [
  { name: 'Inicio', path: '/', icon: Home },
  { name: 'Pacientes', path: '/patients', icon: Users },
  { name: 'Dispositivos', path: '/devices', icon: HeartPulse },
  { name: 'Estudios', path: '/studies', icon: Activity },
  { name: 'Usuarios', path: '/users', icon: UserCog, roles: ['admin'] },
]
