import { LogOut } from 'lucide-react'

import { Avatar, AvatarFallback } from '@/components/ui/avatar'
import { Button } from '@/components/ui/button'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'

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
        <Tooltip delayDuration={150}>
          <TooltipTrigger asChild>
            <Button
              variant="ghost"
              size="icon"
              aria-label="Cerrar sesión"
              className="h-10 w-full rounded-none text-gray-800 hover:bg-primary-50 hover:text-primary-500"
            >
              <LogOut size={24} strokeWidth={1.75} />
            </Button>
          </TooltipTrigger>
          <TooltipContent side="right" sideOffset={12} className="bg-gray-900 text-white">
            Cerrar sesión
          </TooltipContent>
        </Tooltip>

        <Avatar className="h-8 w-8">
          <AvatarFallback className="bg-primary-500 text-body3 font-medium text-white">
            {initials}
          </AvatarFallback>
        </Avatar>
      </div>
    </aside>
  )
}
