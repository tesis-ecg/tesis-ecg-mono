import { useCallback, useState } from 'react'

/**
 * Estado del drawer mobile del sidebar.
 *
 * El sidebar de desktop es de ancho fijo (70px, estilo ican) y no es
 * colapsable. Sólo en mobile (<md) se vuelve un drawer overlay que abre y
 * cierra con el botón hamburguesa del Topbar.
 */
export function useSidebarState() {
  const [mobileOpen, setMobileOpen] = useState(false)

  const openMobile = useCallback(() => setMobileOpen(true), [])
  const closeMobile = useCallback(() => setMobileOpen(false), [])
  const toggleMobile = useCallback(() => setMobileOpen((v) => !v), [])

  return { mobileOpen, setMobileOpen, openMobile, closeMobile, toggleMobile }
}
