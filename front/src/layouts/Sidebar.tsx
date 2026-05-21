import { LogOut } from 'lucide-react'
import { NavItem } from './Sidebar/NavItem'
import { navItems } from './Sidebar/config'

interface SidebarProps {
  /** Cierra el drawer en mobile cuando se hace click en un ítem. */
  onNavigate?: () => void
}

/**
 * Replica del Navbar de ican-web (`src/components/Navbar/index.tsx`):
 * 70px icon-only, vertical, fondo blanco, items + logout + avatar al fondo.
 */
export function Sidebar({ onNavigate }: SidebarProps) {
  // Placeholder: cuando exista AuthContext (TES-9) se reemplaza por user real.
  const initials = 'TS'

  return (
    <aside className="flex h-full w-sidebar shrink-0 flex-col justify-between border-r border-gray-100 bg-white py-4">
      <nav className="flex flex-col gap-1" aria-label="Navegación principal">
        {navItems.map((item) => (
          <NavItem
            key={item.path}
            icon={item.icon}
            label={item.name}
            to={item.path}
            onNavigate={onNavigate}
          />
        ))}
      </nav>

      <div className="flex flex-col items-center gap-3">
        <button
          type="button"
          title="Cerrar sesión"
          aria-label="Cerrar sesión"
          // Logout real lo conecta TES-9.
          onClick={() => {
            // intencionalmente vacío en este pase
          }}
          className="group relative flex h-10 w-full items-center justify-center text-gray-800 transition-colors hover:bg-primary-50 hover:text-primary-500 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-500 focus-visible:ring-inset"
        >
          <LogOut size={24} strokeWidth={1.75} />
          <span
            role="tooltip"
            className="pointer-events-none absolute left-[78px] z-50 whitespace-nowrap rounded-md bg-gray-900 px-2 py-1 text-body3 text-white opacity-0 transition-opacity duration-150 group-hover:opacity-100"
          >
            Cerrar sesión
          </span>
        </button>

        <div
          title="Perfil"
          className="flex h-8 w-8 items-center justify-center rounded-full bg-primary-500 text-body3 font-medium text-white"
        >
          {initials}
        </div>
      </div>
    </aside>
  )
}
