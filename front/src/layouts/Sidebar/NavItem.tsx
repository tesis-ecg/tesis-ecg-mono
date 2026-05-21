import { NavLink } from 'react-router-dom'
import type { LucideIcon } from 'lucide-react'

interface NavItemProps {
  icon: LucideIcon
  label: string
  to: string
  onNavigate?: () => void
}

export function NavItem({ icon: Icon, label, to, onNavigate }: NavItemProps) {
  return (
    <NavLink
      to={to}
      end={to === '/'}
      onClick={onNavigate}
      title={label}
      aria-label={label}
      className={({ isActive }) =>
        [
          'group relative flex h-10 w-full items-center justify-center transition-colors',
          'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-500 focus-visible:ring-inset',
          isActive
            ? 'bg-primary-50 text-primary-500'
            : 'text-gray-800 hover:bg-primary-50 hover:text-primary-500',
        ].join(' ')
      }
    >
      {({ isActive }) => (
        <>
          <Icon size={20} strokeWidth={1.75} />
          {isActive && (
            <span
              aria-hidden
              className="absolute top-0 right-0 h-10 w-1 rounded-l-[10px] bg-primary-300"
            />
          )}
          {/* Tooltip CSS-only */}
          <span
            role="tooltip"
            className="pointer-events-none absolute left-[78px] z-50 whitespace-nowrap rounded-md bg-gray-900 px-2 py-1 text-body3 text-white opacity-0 transition-opacity duration-150 group-hover:opacity-100"
          >
            {label}
          </span>
        </>
      )}
    </NavLink>
  )
}
