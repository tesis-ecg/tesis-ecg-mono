import { Menu, Search, Bell } from 'lucide-react'

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
      <button
        type="button"
        onClick={onMenuClick}
        aria-label="Abrir menú"
        className="flex h-10 w-10 items-center justify-center rounded-md text-gray-800 transition-colors hover:bg-gray-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-500 md:hidden"
      >
        <Menu size={24} strokeWidth={1.75} />
      </button>

      <div className="flex items-center gap-2">
        <span className="text-h6 font-semibold text-primary-500">Holter</span>
        <span className="text-body1 text-gray-600">Dashboard</span>
      </div>

      <div className="mx-4 hidden flex-1 max-w-xl md:block">
        <label className="relative flex items-center">
          <Search
            size={18}
            strokeWidth={1.75}
            className="absolute left-3 text-gray-400"
            aria-hidden
          />
          <input
            type="search"
            placeholder="Buscar paciente, dispositivo, estudio…"
            aria-label="Búsqueda global"
            // funcionalidad real pendiente (otro ticket)
            className="h-10 w-full rounded-lg border border-gray-100 bg-gray-50 pl-10 pr-3 text-body2 text-gray-900 placeholder:text-gray-500 focus:border-primary-300 focus:bg-white focus:outline-none focus:ring-2 focus:ring-primary-100"
          />
        </label>
      </div>

      <div className="ml-auto flex items-center gap-2">
        <button
          type="button"
          aria-label="Notificaciones"
          className="relative flex h-10 w-10 items-center justify-center rounded-md text-gray-800 transition-colors hover:bg-gray-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-500"
        >
          <Bell size={20} strokeWidth={1.75} />
        </button>
      </div>
    </header>
  )
}
