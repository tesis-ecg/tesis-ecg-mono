import { Maximize, ZoomIn, ZoomOut } from 'lucide-react'

import { Button } from '@/components/ui/button'

interface ECGZoomControlsProps {
  onZoomIn: () => void
  onZoomOut: () => void
  /** Navega a la página ampliada del visor. */
  onFullscreen?: () => void
  className?: string
}

const OVERLAY_BUTTON = 'size-8 border border-border bg-card text-fg hover:bg-muted'

/** Controles del visor, conectados por el padre a su API imperativa. */
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
      </div>
    </div>
  )
}
