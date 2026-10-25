import { useMemo, useRef } from 'react'
import { useLocation, useNavigate, useSearchParams } from 'react-router-dom'

import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { focusViewerOnAnnotation } from '@/features/ecg/annotationMeta'
import { ECGExpandedView } from '@/features/ecg/components/ECGExpandedView'
import { ECGFindingsPanel } from '@/features/ecg/components/ECGFindingsPanel'
import { ECGMinimap } from '@/features/ecg/components/ECGMinimap'
import { ECGPaperControls } from '@/features/ecg/components/ECGPaperControls'
import { ECGViewer } from '@/features/ecg/components/ECGViewer'
import { ECGZoomControls } from '@/features/ecg/components/ECGZoomControls'
import { useEcgSession } from '@/features/ecg/hooks/useEcgSession'
import { mockEcgSignal } from '@/features/ecg/mocks'
import { annotationIsInGroup } from '@/features/ecg/timeline'
import type { ECGAnnotation, ECGViewerHandle, ECGViewportChange } from '@/features/ecg/types'

/**
 * Página de desarrollo para validar el `<ECGViewer />` aislado de toda lógica
 * de estudios. Útil para benchmarks de performance del componente y para que el
 * reviewer del PR pueda probar TES-22/TES-23 sin necesidad de navegar el flujo
 * completo desde pacientes.
 *
 * Ruta: `/__dev/ecg-viewer` (protegida por `<ProtectedRoute />`).
 */
export function DevEcgViewer({ expanded = false }: { expanded?: boolean }) {
  const navigate = useNavigate()
  const location = useLocation()
  const [search, setSearch] = useSearchParams()
  const seed = search.get('seed') ?? 'dev-seed-0'
  const setSeed = (next: string) => setSearch({ seed: next })

  const signal = useMemo(() => mockEcgSignal(seed), [seed])

  const sampleCount = signal.samples.length
  const durationMin = sampleCount / signal.sampleRate / 60
  const durationMs = (sampleCount / signal.sampleRate) * 1000

  const viewerRef = useRef<ECGViewerHandle | null>(null)
  const session = useEcgSession()
  const {
    scale,
    viewport,
    setViewport,
    cursorMs,
    setCursorMs,
    selectedAnnotationId,
    setSelectedAnnotationId,
  } = session
  const goExpanded = () =>
    navigate(
      { pathname: '/__dev/ecg-viewer/expanded', search: search.toString() },
      { state: { fromPlayground: true } },
    )
  const goBack = () => {
    if (location.state?.fromPlayground === true) navigate(-1)
    else navigate({ pathname: '/__dev/ecg-viewer', search: search.toString() }, { replace: true })
  }

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
  const handleAnnotationSelect = (annotation: ECGAnnotation) => {
    if (session.selectedGroup && !annotationIsInGroup(annotation, session.selectedGroup))
      session.setSelectedGroup(null)
    focusViewerOnAnnotation(viewerRef.current, annotation)
    setSelectedAnnotationId(annotation.id)
  }

  if (expanded) {
    return (
      <div className="flex h-full min-h-0 flex-col">
        <ECGExpandedView
          timelineWindow={session.timelineWindow}
          onTimelineWindowChange={session.setTimelineWindow}
          selectedGroup={session.selectedGroup}
          onGroupChange={session.setSelectedGroup}
          key={seed}
          signal={signal}
          initialViewport={viewport}
          initialCursorMs={cursorMs}
          onViewportChange={setViewport}
          onCursorChange={setCursorMs}
          onBack={goBack}
          backLabel="Volver al visor"
          initialSelectedAnnotationId={selectedAnnotationId}
          paperSpeed={scale.paperSpeed}
          amplitude={scale.amplitude}
          onPaperSpeedChange={scale.setPaperSpeed}
          onAmplitudeChange={scale.setAmplitude}
          onAnnotationSelect={setSelectedAnnotationId}
          showAnnotations={session.showAnnotationsOnChart}
          onShowAnnotationsChange={session.setShowAnnotationsOnChart}
        />
      </div>
    )
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
          <ECGZoomControls
            onZoomIn={handleZoomIn}
            onZoomOut={handleZoomOut}
            onFullscreen={goExpanded}
          />
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

          <ECGPaperControls
            paperSpeed={scale.paperSpeed}
            amplitude={scale.amplitude}
            onPaperSpeedChange={scale.setPaperSpeed}
            onAmplitudeChange={scale.setAmplitude}
            onScale={scale.onScale}
            onResetScale={() => {
              viewerRef.current?.resetScale()
              scale.setOnScale(true)
            }}
          />

          <ECGMinimap
            timelineWindow={session.timelineWindow}
            onTimelineWindowChange={session.setTimelineWindow}
            selectedGroup={session.selectedGroup}
            onGroupSelect={session.setSelectedGroup}
            showAnnotations={session.showAnnotationsOnChart}
            signal={signal}
            viewport={viewport}
            onViewportChange={handleMinimapChange}
            selectedAnnotationId={selectedAnnotationId}
            onAnnotationSelect={handleAnnotationSelect}
          />

          <div className="grid min-w-0 gap-4 lg:grid-cols-[minmax(0,1fr)_320px]">
            <div className="min-w-0">
              <ECGViewer
                key={seed}
                ref={viewerRef}
                signal={signal}
                height={400}
                paperSpeed={scale.paperSpeed}
                amplitude={scale.amplitude}
                initialViewport={viewport ?? undefined}
                initialCursorMs={cursorMs ?? undefined}
                onCursorChange={setCursorMs}
                onViewportChange={setViewport}
                onScaleMatchChange={scale.setOnScale}
                selectedAnnotationId={selectedAnnotationId}
                onAnnotationSelect={handleAnnotationSelect}
                showAnnotations={session.showAnnotationsOnChart}
              />
            </div>
            <ECGFindingsPanel
              annotations={signal.annotations}
              recordingStartMs={signal.startTimestamp}
              selectedAnnotationId={selectedAnnotationId}
              onAnnotationSelect={handleAnnotationSelect}
              selectedGroup={session.selectedGroup}
              onGroupChange={session.setSelectedGroup}
              showOnChart={session.showAnnotationsOnChart}
              onShowOnChartChange={session.setShowAnnotationsOnChart}
              className="max-h-[32rem]"
            />
          </div>

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
