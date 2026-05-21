import { NavLink } from 'react-router-dom'
import type { LucideIcon } from 'lucide-react'

import { cn } from '@/lib/utils'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'

interface NavItemProps {
  icon: LucideIcon
  label: string
  to: string
  onNavigate?: () => void
}

export function NavItem({ icon: Icon, label, to, onNavigate }: NavItemProps) {
  return (
    <Tooltip delayDuration={150}>
      <TooltipTrigger asChild>
        <NavLink
          to={to}
          end={to === '/'}
          onClick={onNavigate}
          aria-label={label}
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
        {label}
      </TooltipContent>
    </Tooltip>
  )
}
