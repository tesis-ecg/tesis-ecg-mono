import { Maximize, Minimize, ZoomIn, ZoomOut } from 'lucide-react'

import { Button } from '@/components/ui/button'

interface ECGZoomControlsProps {
  onZoomIn: () => void
  onZoomOut: () => void
  /** Abre el viewer en una modal a pantalla completa para revisión cómoda. */
  onFullscreen?: () => void
  /** Cierra la modal de pantalla completa (sustituye al botón fullscreen cuando está dentro). */
  onMinimize?: () => void
  className?: string
}

// Flotan sobre la traza: translúcidos para no tapar lo que hay debajo, y
// opacos al pasar el mouse para que se lean cuando se los va a usar.
const OVERLAY_BUTTON =
  'border border-border/60 bg-card/60 text-fg shadow-sm backdrop-blur-sm hover:bg-card/95'

/**
 * Controles de zoom para el `<ECGViewer />`. Stateless — el padre conecta los
 * callbacks contra la API imperativa del viewer (typ. `zoomToRange`) y el
 * estado del Dialog de pantalla completa.
 *
 * El botón Maximize **abre el viewer en una modal grande**, no resetea el
 * zoom. Para volver a ver la señal completa, hay que arrastrar el viewport del
 * mini-mapa o hacer zoom out repetido.
 */
export function ECGZoomControls({
  onZoomIn,
  onZoomOut,
  onFullscreen,
  onMinimize,
  className,
}: ECGZoomControlsProps) {
  return (
    <div className={className}>
      <div className="inline-flex gap-1">
        <Button
          variant="secondary"
          size="icon"
          className={OVERLAY_BUTTON}
          onClick={onZoomOut}
          aria-label="Zoom out"
          title="Zoom out"
        >
          <ZoomOut className="size-4" />
        </Button>
        <Button
          variant="secondary"
          size="icon"
          className={OVERLAY_BUTTON}
          onClick={onZoomIn}
          aria-label="Zoom in"
          title="Zoom in"
        >
          <ZoomIn className="size-4" />
        </Button>
        {onFullscreen && (
          <Button
            variant="secondary"
            size="icon"
            className={OVERLAY_BUTTON}
            onClick={onFullscreen}
            aria-label="Abrir en pantalla completa"
            title="Pantalla completa"
          >
            <Maximize className="size-4" />
          </Button>
        )}
        {onMinimize && (
          <Button
            variant="secondary"
            size="icon"
            className={OVERLAY_BUTTON}
            onClick={onMinimize}
            aria-label="Minimizar"
            title="Minimizar"
          >
            <Minimize className="size-4" />
          </Button>
        )}
      </div>
    </div>
  )
}
