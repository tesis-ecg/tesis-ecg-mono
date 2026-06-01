import { Maximize, ZoomIn, ZoomOut } from 'lucide-react'

import { Button } from '@/components/ui/button'

interface ECGZoomControlsProps {
  onZoomIn: () => void
  onZoomOut: () => void
  /** Abre el viewer en una modal a pantalla completa para revisión cómoda. */
  onFullscreen?: () => void
  className?: string
}

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
  className,
}: ECGZoomControlsProps) {
  return (
    <div className={className}>
      <div className="inline-flex gap-1">
        <Button
          variant="secondary"
          size="icon"
          onClick={onZoomOut}
          aria-label="Zoom out"
          title="Zoom out"
        >
          <ZoomOut className="size-4" />
        </Button>
        <Button
          variant="secondary"
          size="icon"
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
            onClick={onFullscreen}
            aria-label="Abrir en pantalla completa"
            title="Pantalla completa"
          >
            <Maximize className="size-4" />
          </Button>
        )}
      </div>
    </div>
  )
}
