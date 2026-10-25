import { Menu } from 'lucide-react'
import { Outlet, useMatch } from 'react-router-dom'

import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'
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
  const studyEcg = useMatch('/studies/:id/ecg')
  const devEcg = useMatch('/__dev/ecg-viewer/expanded')
  const expandedViewer = !!studyEcg || !!devEcg
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
          {expandedViewer ? (
            <div className="flex h-11 shrink-0 items-center border-b border-border bg-card px-3 md:hidden">
              <Button variant="ghost" size="icon" onClick={openMobile} aria-label="Abrir menú">
                <Menu className="size-5" aria-hidden />
              </Button>
              <span className="text-body3 ml-2 text-fg-muted">Visor ECG</span>
            </div>
          ) : (
            <Topbar onMenuClick={openMobile} />
          )}
          <main
            className={cn(
              'min-h-0 flex-1 overflow-y-auto bg-bg-muted',
              expandedViewer ? 'p-3 lg:p-5' : 'p-6',
            )}
          >
            <Outlet />
          </main>
        </div>
      </div>
    </TooltipProvider>
  )
}
