import { Outlet } from 'react-router-dom'
import { Sidebar } from './Sidebar'
import { Topbar } from './Topbar'
import { useSidebarState } from '../hooks/useSidebarState'

/**
 * Layout principal de las rutas autenticadas: sidebar a la izquierda
 * (réplica del Navbar de ican: 70px icon-only), topbar arriba, outlet
 * en el resto.
 *
 * En <md el sidebar se vuelve drawer overlay activado por el botón
 * hamburguesa del topbar.
 */
export function AppShell() {
  const { mobileOpen, openMobile, closeMobile } = useSidebarState()

  return (
    <div className="flex h-screen w-screen overflow-hidden bg-bg text-fg">
      {/* Sidebar desktop */}
      <div className="hidden md:flex">
        <Sidebar />
      </div>

      {/* Drawer mobile */}
      {mobileOpen && (
        <>
          <div
            className="fixed inset-0 z-40 bg-black/30 md:hidden"
            onClick={closeMobile}
            aria-hidden
          />
          <div className="fixed inset-y-0 left-0 z-50 md:hidden">
            <Sidebar onNavigate={closeMobile} />
          </div>
        </>
      )}

      <div className="flex min-w-0 flex-1 flex-col">
        <Topbar onMenuClick={openMobile} />
        <main className="flex-1 overflow-y-auto bg-bg-muted p-6">
          <Outlet />
        </main>
      </div>
    </div>
  )
}
