import { Home, Users, Shirt, Activity, AlertTriangle, UserCog, Radio } from 'lucide-react'
import type { LucideIcon } from 'lucide-react'

import type { Role } from '@/features/auth/types'

export type NavItemConfig = {
  name: string
  path: string
  icon: LucideIcon
  /** Si se define, el ítem solo se muestra a estos roles (admin siempre ve todo). */
  roles?: Role[]
  /** Muestra el contador de alertas pendientes sobre el ícono. */
  badge?: 'pendingAlerts'
}

export const navItems: NavItemConfig[] = [
  { name: 'Inicio', path: '/', icon: Home },
  { name: 'Pacientes', path: '/patients', icon: Users },
  { name: 'Dispositivos', path: '/devices', icon: Shirt },
  { name: 'Estudios', path: '/studies', icon: Activity },
  { name: 'Alertas', path: '/alerts', icon: AlertTriangle, badge: 'pendingAlerts' },
  { name: 'Usuarios', path: '/users', icon: UserCog, roles: ['admin'] },
  // Manda señal real al endpoint de ingesta y rota API keys de equipos: solo
  // admin. Antes no estaba en el menú y había que saber la URL de memoria.
  { name: 'Simulador de chalecos', path: '/__sim/vest', icon: Radio, roles: ['admin'] },
]
