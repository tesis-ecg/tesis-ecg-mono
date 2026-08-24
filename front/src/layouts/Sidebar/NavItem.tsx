import { NavLink } from 'react-router-dom'
import type { LucideIcon } from 'lucide-react'

import { cn } from '@/lib/utils'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'

interface NavItemProps {
  icon: LucideIcon
  label: string
  to: string
  onNavigate?: () => void
  /** Contador sobre el ícono. `0` o `undefined` no renderiza nada. */
  badgeCount?: number
}

export function NavItem({ icon: Icon, label, to, onNavigate, badgeCount }: NavItemProps) {
  const showBadge = typeof badgeCount === 'number' && badgeCount > 0
  // El sidebar es icon-only: sin el conteo en el aria-label, un lector de
  // pantalla anuncia "Alertas" y se pierde que hay trabajo pendiente.
  const accessibleLabel = showBadge ? `${label} (${badgeCount} pendientes)` : label

  return (
    <Tooltip delayDuration={150}>
      <TooltipTrigger asChild>
        <NavLink
          to={to}
          end={to === '/'}
          onClick={onNavigate}
          aria-label={accessibleLabel}
          className={({ isActive }) =>
            cn(
              'group relative flex h-10 w-full items-center justify-center transition-colors',
              'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-inset',
              isActive
                ? 'bg-primary-50 text-primary-500'
                : 'text-gray-800 hover:bg-primary-50 hover:text-primary-500',
            )
          }
        >
          {({ isActive }) => (
            <>
              <Icon size={20} strokeWidth={1.75} />
              {showBadge && (
                <span
                  aria-hidden
                  className="absolute top-1 right-2 min-w-4 rounded-full bg-destructive px-1 text-center text-[10px] leading-4 font-medium text-destructive-foreground"
                >
                  {badgeCount > 99 ? '99+' : badgeCount}
                </span>
              )}
              {isActive && (
                <span
                  aria-hidden
                  className="absolute right-0 top-0 h-10 w-1 rounded-l-[10px] bg-primary-300"
                />
              )}
            </>
          )}
        </NavLink>
      </TooltipTrigger>
      <TooltipContent side="right" sideOffset={12} className="bg-gray-900 text-white">
        {accessibleLabel}
      </TooltipContent>
    </Tooltip>
  )
}
