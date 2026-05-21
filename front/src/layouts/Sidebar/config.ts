import { Home, Users, HeartPulse, Activity, FlaskConical, Settings } from 'lucide-react'
import type { LucideIcon } from 'lucide-react'

export type NavItemConfig = {
  name: string
  path: string
  icon: LucideIcon
}

export const navItems: NavItemConfig[] = [
  { name: 'Inicio', path: '/', icon: Home },
  { name: 'Pacientes', path: '/patients', icon: Users },
  { name: 'Dispositivos', path: '/devices', icon: HeartPulse },
  { name: 'Estudios', path: '/studies', icon: Activity },
  { name: 'Investigación', path: '/research', icon: FlaskConical },
  { name: 'Configuración', path: '/settings', icon: Settings },
]
