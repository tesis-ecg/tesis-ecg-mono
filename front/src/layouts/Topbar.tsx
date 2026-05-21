import { Menu, Search, Bell } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'

interface TopbarProps {
  onMenuClick: () => void
}

/**
 * Barra horizontal arriba del outlet. Ican no la tenía; la sumamos para
 * exponer búsqueda global y notificaciones, y para hostear el botón
 * hamburguesa que abre el drawer del sidebar en mobile.
 */
export function Topbar({ onMenuClick }: TopbarProps) {
  return (
    <header className="flex h-topbar shrink-0 items-center gap-3 border-b border-gray-100 bg-white px-6">
      <Button
        variant="ghost"
        size="icon"
        onClick={onMenuClick}
        aria-label="Abrir menú"
        className="text-gray-800 md:hidden"
      >
        <Menu size={24} strokeWidth={1.75} />
      </Button>

      <div className="flex items-center gap-2">
        <span className="text-h6 font-semibold text-primary-500">Holter</span>
        <span className="text-body1 text-gray-600">Dashboard</span>
      </div>

      <div className="mx-4 hidden max-w-xl flex-1 md:block">
        <label className="relative flex items-center">
          <Search
            size={18}
            strokeWidth={1.75}
            className="absolute left-3 text-gray-400"
            aria-hidden
          />
          <Input
            type="search"
            placeholder="Buscar paciente, dispositivo, estudio…"
            aria-label="Búsqueda global"
            className="h-10 border-gray-100 bg-gray-50 pl-10 text-body2 text-gray-900 placeholder:text-gray-500 focus-visible:border-primary-300 focus-visible:bg-white focus-visible:ring-primary-100"
          />
        </label>
      </div>

      <div className="ml-auto flex items-center gap-2">
        <Button variant="ghost" size="icon" aria-label="Notificaciones" className="text-gray-800">
          <Bell size={20} strokeWidth={1.75} />
        </Button>
      </div>
    </header>
  )
}
