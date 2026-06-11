import type { KeyboardEvent } from 'react'
import type { NavigateFunction } from 'react-router-dom'

/**
 * Props para una fila de tabla clickeable y accesible (mismo patrón que
 * `features/patients/components/StudiesTable.tsx`). Navega al destino con
 * click, Enter o Espacio.
 */
export function rowNavProps(navigate: NavigateFunction, to: string, ariaLabel: string) {
  return {
    onClick: () => navigate(to),
    onKeyDown: (e: KeyboardEvent) => {
      if (e.key === 'Enter' || e.key === ' ') {
        e.preventDefault()
        navigate(to)
      }
    },
    tabIndex: 0,
    role: 'button' as const,
    'aria-label': ariaLabel,
    className:
      'cursor-pointer focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-primary-500 focus-visible:outline-none',
  }
}
