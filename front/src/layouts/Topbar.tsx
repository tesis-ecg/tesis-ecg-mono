import { Menu } from 'lucide-react'

import { Button } from '@/components/ui/button'

interface TopbarProps {
  onMenuClick: () => void
}

/**
 * Barra horizontal arriba del outlet. Ican no la tenía; la sumamos para
 * hostear la identidad del producto y el botón hamburguesa mobile.
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

      <div className="ml-auto" />
    </header>
  )
}
