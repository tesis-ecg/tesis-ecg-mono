import { useEffect, useRef, useState } from 'react'

import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'

import type { ECGSignal, ECGViewerHandle, ECGViewportChange } from '../types'
import { ECGMinimap } from './ECGMinimap'
import { ECGViewer } from './ECGViewer'
import { ECGZoomControls } from './ECGZoomControls'

/**
 * Espacio reservado dentro del slot del viewer para la legend nativa de uPlot
 * (label de "Tiempo" + "ECG" abajo del canvas). Sin esto, el canvas + legend
 * se desbordan sobre el texto de indicaciones de abajo.
 */
const UPLOT_LEGEND_RESERVE_PX = 48

interface ECGFullscreenDialogProps {
  signal: ECGSignal
  /** Viewport actual del viewer chico — la modal arranca mostrando lo mismo. */
  initialViewport: ECGViewportChange | null
  open: boolean
  onOpenChange: (open: boolean) => void
  /**
   * Si está provisto, se invoca con el último viewport antes de cerrar — útil
   * para que el viewer chico recupere la posición del viewer grande.
   */
  onClose?: (lastViewport: ECGViewportChange | null) => void
}

/**
 * Abre el `<ECGViewer />` en una modal a pantalla casi completa (95vw × 90vh)
 * para que el médico pueda revisar la señal con espacio. Es una instancia
 * nueva de uPlot — Radix unmonta el contenido al cerrar, así que no hay leak.
 *
 * El viewport del viewer chico se pasa como `initialViewport` para que la
 * modal arranque mirando exactamente lo mismo. Al cerrar (botón Minimize,
 * `Esc` o click fuera), opcionalmente se propaga el viewport final via
 * `onClose`.
 *
 * No hay X de cierre — el control de cerrar vive en el `ECGZoomControls`
 * arriba a la derecha junto al zoom in/out (botón Minimize).
 */
export function ECGFullscreenDialog({
  signal,
  initialViewport,
  open,
  onOpenChange,
  onClose,
}: ECGFullscreenDialogProps) {
  // Snapshot del viewport entregado por el padre cuando se abre la modal.
  // Sobrevive en una ref para que `onClose` lo pueda usar después de que el
  // body se desmontó.
  const closeViewportRef = useRef<ECGViewportChange | null>(initialViewport)

  const closeDialog = () => {
    onClose?.(closeViewportRef.current)
    onOpenChange(false)
  }

  const handleOpenChange = (nextOpen: boolean) => {
    if (!nextOpen) {
      onClose?.(closeViewportRef.current)
    }
    onOpenChange(nextOpen)
  }

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent
        showCloseButton={false}
        className="flex h-[90vh] max-w-none flex-col gap-3 p-4 sm:max-w-none md:w-[95vw]"
      >
        {/* El body vive en un sub-componente que se monta/desmonta con `open`.
            Eso garantiza que el estado interno (viewport actual) se resetea en
            cada apertura sin tener que usar setState dentro de un effect. */}
        {open && (
          <ECGFullscreenBody
            signal={signal}
            initialViewport={initialViewport}
            onViewportChange={(vp) => {
              closeViewportRef.current = vp
            }}
            onMinimize={closeDialog}
          />
        )}
      </DialogContent>
    </Dialog>
  )
}

interface ECGFullscreenBodyProps {
  signal: ECGSignal
  initialViewport: ECGViewportChange | null
  onViewportChange: (viewport: ECGViewportChange) => void
  onMinimize: () => void
}

function ECGFullscreenBody({
  signal,
  initialViewport,
  onViewportChange,
  onMinimize,
}: ECGFullscreenBodyProps) {
  const viewerRef = useRef<ECGViewerHandle | null>(null)
  const viewerSlotRef = useRef<HTMLDivElement | null>(null)
  const [viewport, setViewport] = useState<ECGViewportChange | null>(initialViewport)
  const [viewerHeight, setViewerHeight] = useState(360)

  // Mide el slot del viewer y descuenta espacio para la legend de uPlot. Sin
  // esto, el canvas se renderiza más alto que el espacio disponible y la
  // legend choca con el texto de indicaciones.
  useEffect(() => {
    const el = viewerSlotRef.current
    if (!el) return
    const update = () => {
      const next = Math.max(300, Math.floor(el.clientHeight - UPLOT_LEGEND_RESERVE_PX))
      setViewerHeight(next)
    }
    update()
    const ro = new ResizeObserver(update)
    ro.observe(el)
    return () => ro.disconnect()
  }, [])

  const fullSpanMs = (signal.samples.length / signal.sampleRate) * 1000

  const handleViewportChange = (next: ECGViewportChange) => {
    setViewport(next)
    onViewportChange(next)
  }

  const handleZoomIn = () => {
    if (!viewport) return
    const span = viewport.endMs - viewport.startMs
    const center = (viewport.startMs + viewport.endMs) / 2
    const newSpan = Math.max(500, span / 2)
    viewerRef.current?.zoomToRange(center - newSpan / 2, center + newSpan / 2)
  }
  const handleZoomOut = () => {
    if (!viewport) return
    const span = viewport.endMs - viewport.startMs
    const center = (viewport.startMs + viewport.endMs) / 2
    const newSpan = Math.min(fullSpanMs, span * 2)
    viewerRef.current?.zoomToRange(center - newSpan / 2, center + newSpan / 2)
  }
  const handleMinimapChange = (next: ECGViewportChange) => {
    viewerRef.current?.zoomToRange(next.startMs, next.endMs)
  }

  return (
    <>
      <DialogHeader className="flex flex-row items-center justify-between gap-2">
        <div className="flex flex-col gap-0.5">
          <DialogTitle>Señal ECG</DialogTitle>
          <DialogDescription>
            Vista ampliada del estudio — el zoom y la posición vuelven al viewer principal al
            cerrar.
          </DialogDescription>
        </div>
        <ECGZoomControls
          onZoomIn={handleZoomIn}
          onZoomOut={handleZoomOut}
          onMinimize={onMinimize}
        />
      </DialogHeader>

      <ECGMinimap signal={signal} viewport={viewport} onViewportChange={handleMinimapChange} />

      <div ref={viewerSlotRef} className="min-h-0 flex-1">
        <ECGViewer
          ref={viewerRef}
          signal={signal}
          height={viewerHeight}
          initialViewport={initialViewport ?? undefined}
          onViewportChange={handleViewportChange}
        />
      </div>

      <p className="text-body3 text-gray-500">
        Zoom: <kbd className="rounded border border-border bg-muted px-1">Ctrl/⌘ + scroll</kbd> ·
        Pan: drag o flechas izq/der con focus en el gráfico · Cerrar:{' '}
        <kbd className="rounded border border-border bg-muted px-1">Esc</kbd>
      </p>
    </>
  )
}
