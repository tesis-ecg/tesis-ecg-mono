import { useMemo, useRef, useState } from 'react'

import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { ECGMinimap } from '@/features/ecg/components/ECGMinimap'
import { ECGViewer } from '@/features/ecg/components/ECGViewer'
import { ECGZoomControls } from '@/features/ecg/components/ECGZoomControls'
import { mockEcgSignal } from '@/features/ecg/mocks'
import type { ECGViewerHandle, ECGViewportChange } from '@/features/ecg/types'

/**
 * Página de desarrollo para validar el `<ECGViewer />` aislado de toda lógica
 * de estudios. Útil para benchmarks de performance del componente y para que el
 * reviewer del PR pueda probar TES-22/TES-23 sin necesidad de navegar el flujo
 * completo desde pacientes.
 *
 * Ruta: `/__dev/ecg-viewer` (protegida por `<ProtectedRoute />`).
 */
export function DevEcgViewer() {
  const [seed, setSeed] = useState('dev-seed-0')

  const signal = useMemo(() => mockEcgSignal(seed), [seed])

  const sampleCount = signal.samples.length
  const durationMin = sampleCount / signal.sampleRate / 60
  const durationMs = (sampleCount / signal.sampleRate) * 1000

  const viewerRef = useRef<ECGViewerHandle | null>(null)
  const [viewport, setViewport] = useState<ECGViewportChange | null>(null)

  // Demos de la API imperativa (TES-23).
  const handleJumpTo30s = () => {
    viewerRef.current?.jumpTo(signal.startTimestamp + 30_000)
  }
  const handleZoomToTenToTwenty = () => {
    viewerRef.current?.zoomToRange(signal.startTimestamp + 10_000, signal.startTimestamp + 20_000)
  }
  const handleReset = () => {
    viewerRef.current?.resetZoom()
  }

  // Conexión zoom controls → API imperativa.
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
    const newSpan = Math.min(durationMs, span * 2)
    viewerRef.current?.zoomToRange(center - newSpan / 2, center + newSpan / 2)
  }

  // Sincronización mini-mapa → viewer.
  const handleMinimapChange = (next: ECGViewportChange) => {
    viewerRef.current?.zoomToRange(next.startMs, next.endMs)
  }

  return (
    <div className="flex flex-col gap-4">
      <header className="flex flex-col gap-1">
        <h1 className="text-h4 text-gray-900">ECG Viewer — Dev playground</h1>
        <p className="text-body2 text-gray-600">
          Renderiza una señal sintética para validar performance, interacción (zoom con Ctrl+scroll,
          pan con drag, flechas izq/der con focus) y la API imperativa.
        </p>
      </header>

      <Card>
        <CardHeader className="flex flex-row items-center justify-between">
          <CardTitle className="text-h6">
            Señal mock — {sampleCount.toLocaleString('es-AR')} samples · {durationMin.toFixed(0)}{' '}
            min · {signal.sampleRate} Hz
          </CardTitle>
          <ECGZoomControls onZoomIn={handleZoomIn} onZoomOut={handleZoomOut} onFit={handleReset} />
        </CardHeader>
        <CardContent className="flex flex-col gap-3">
          <div className="flex flex-wrap gap-2">
            <Button
              variant="secondary"
              onClick={() => setSeed(`dev-seed-${Math.floor(performance.now())}`)}
            >
              Regenerar señal
            </Button>
            <Button variant="outline" onClick={handleJumpTo30s}>
              Jump a 30 s
            </Button>
            <Button variant="outline" onClick={handleZoomToTenToTwenty}>
              Zoom 10 s – 20 s
            </Button>
            <Button variant="outline" onClick={handleReset}>
              Reset
            </Button>
          </div>

          <ECGMinimap signal={signal} viewport={viewport} onViewportChange={handleMinimapChange} />

          <ECGViewer ref={viewerRef} signal={signal} height={400} onViewportChange={setViewport} />

          {viewport && (
            <p className="text-body3 mt-10 text-gray-600">
              Viewport actual: {((viewport.startMs - signal.startTimestamp) / 1000).toFixed(2)} s →{' '}
              {((viewport.endMs - signal.startTimestamp) / 1000).toFixed(2)} s
            </p>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
