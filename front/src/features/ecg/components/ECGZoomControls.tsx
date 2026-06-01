import { Maximize, ZoomIn, ZoomOut } from 'lucide-react'

import { Button } from '@/components/ui/button'

interface ECGZoomControlsProps {
  onZoomIn: () => void
  onZoomOut: () => void
  onFit: () => void
  className?: string
}

/**
 * Controles de zoom para el `<ECGViewer />`. Stateless — el padre conecta los
 * callbacks contra la API imperativa del viewer (típicamente `zoomToRange` y
 * `resetZoom`).
 */
export function ECGZoomControls({ onZoomIn, onZoomOut, onFit, className }: ECGZoomControlsProps) {
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
        <Button
          variant="secondary"
          size="icon"
          onClick={onFit}
          aria-label="Ajustar a la señal completa"
          title="Fit"
        >
          <Maximize className="size-4" />
        </Button>
      </div>
    </div>
  )
}
