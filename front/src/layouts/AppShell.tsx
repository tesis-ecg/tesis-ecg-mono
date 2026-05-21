import { Outlet } from 'react-router-dom'

import { Sheet, SheetContent } from '@/components/ui/sheet'
import { TooltipProvider } from '@/components/ui/tooltip'

import { Sidebar } from './Sidebar'
import { Topbar } from './Topbar'
import { useSidebarState } from '../hooks/useSidebarState'

/**
 * Layout principal de las rutas autenticadas: sidebar a la izquierda
 * (réplica del Navbar de ican: 70px icon-only), topbar arriba, outlet
 * en el resto.
 *
 * En <md el sidebar se vuelve drawer overlay activado por el botón
 * hamburguesa del topbar (Sheet de shadcn).
 */
export function AppShell() {
  const { mobileOpen, openMobile, closeMobile, setMobileOpen } = useSidebarState()

  return (
    <TooltipProvider>
      <div className="flex h-screen w-screen overflow-hidden bg-bg text-fg">
        {/* Sidebar desktop */}
        <div className="hidden md:flex">
          <Sidebar />
        </div>

        {/* Drawer mobile vía Sheet */}
        <Sheet open={mobileOpen} onOpenChange={setMobileOpen}>
          <SheetContent
            side="left"
            hideCloseButton
            className="w-sidebar max-w-sidebar p-0 md:hidden"
          >
            <Sidebar onNavigate={closeMobile} />
          </SheetContent>
        </Sheet>

        <div className="flex min-w-0 flex-1 flex-col">
          <Topbar onMenuClick={openMobile} />
          <main className="flex-1 overflow-y-auto bg-bg-muted p-6">
            <Outlet />
          </main>
        </div>
      </div>
    </TooltipProvider>
  )
}
