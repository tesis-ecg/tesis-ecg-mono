import { EllipsisVertical, type LucideIcon } from 'lucide-react'

import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'

export interface KebabMenuAction {
  label: string
  icon?: LucideIcon
  onSelect: () => void
  variant?: 'default' | 'destructive'
  disabled?: boolean
}

interface KebabMenuProps {
  actions: KebabMenuAction[]
  label?: string
  align?: 'start' | 'center' | 'end'
}

/**
 * Botón "3 puntitos" con un menú de acciones. Es el patrón para opciones de
 * gestión secundarias (editar, eliminar) en headers de páginas de detalle y
 * en filas de tabla.
 */
export function KebabMenu({ actions, label = 'Acciones', align = 'end' }: KebabMenuProps) {
  return (
    <DropdownMenu>
      <DropdownMenuTrigger
        aria-label={label}
        className="inline-flex size-9 cursor-pointer items-center justify-center rounded-md text-gray-600 transition-colors hover:bg-gray-50 hover:text-gray-900 focus-visible:ring-2 focus-visible:ring-primary-500 focus-visible:outline-none"
      >
        <EllipsisVertical className="size-4" aria-hidden />
      </DropdownMenuTrigger>
      <DropdownMenuContent align={align}>
        {actions.map((action) => {
          const Icon = action.icon
          return (
            <DropdownMenuItem
              key={action.label}
              variant={action.variant}
              disabled={action.disabled}
              onSelect={(e) => {
                e.stopPropagation()
                action.onSelect()
              }}
            >
              {Icon ? <Icon aria-hidden /> : null}
              {action.label}
            </DropdownMenuItem>
          )
        })}
      </DropdownMenuContent>
    </DropdownMenu>
  )
}
