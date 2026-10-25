import { ArrowLeft } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'

import { Button } from '@/components/ui/button'
import type { StudyFindings } from '@/features/studies/types'

import { focusViewerOnAnnotation } from '../annotationMeta'
import type { AmplitudeMode, PaperSpeed } from '../paperScale'
import type {
  ECGAnnotation,
  ECGAnnotationGroup,
  ECGSignal,
  ECGTimelineRange,
  ECGViewerHandle,
  ECGViewportChange,
} from '../types'
import { annotationIsInGroup } from '../timeline'
import { ECGFindingsPanel } from './ECGFindingsPanel'
import { ECGMinimap } from './ECGMinimap'
import { ECGPaperControls } from './ECGPaperControls'
import { ECGViewer } from './ECGViewer'
import { ECGZoomControls } from './ECGZoomControls'

// La barra de herramientas (48 px) y la leyenda de uPlot (48 px) ocupan espacio propio.
const UPLOT_LEGEND_RESERVE_PX = 96

interface ECGExpandedViewProps {
  reclassification?: { studyId: string; findings: StudyFindings | undefined }
  signal: ECGSignal
  initialViewport: ECGViewportChange | null
  initialCursorMs: number | null
  onViewportChange: (viewport: ECGViewportChange) => void
  onCursorChange: (cursorMs: number) => void
  onBack: () => void
  backLabel?: string
  initialSelectedAnnotationId: string | null
  paperSpeed: PaperSpeed
  amplitude: AmplitudeMode
  onPaperSpeedChange: (value: PaperSpeed) => void
  onAmplitudeChange: (value: AmplitudeMode) => void
  onAnnotationSelect?: (annotationId: string) => void
  followLatest?: boolean
  /** Avisos dibujados sobre el gráfico; compartido con la vista principal. */
  showAnnotations?: boolean
  onShowAnnotationsChange?: (show: boolean) => void
  timelineWindow?: ECGTimelineRange | null
  onTimelineWindowChange?: (range: ECGTimelineRange) => void
  selectedGroup?: ECGAnnotationGroup | null
  onGroupChange?: (group: ECGAnnotationGroup | null) => void
}

export function ECGExpandedView({
  reclassification,
  signal,
  initialViewport,
  initialCursorMs,
  onViewportChange,
  onCursorChange,
  onBack,
  backLabel = 'Volver al estudio',
  initialSelectedAnnotationId,
  paperSpeed,
  amplitude,
  onPaperSpeedChange,
  onAmplitudeChange,
  onAnnotationSelect,
  followLatest = false,
  showAnnotations = true,
  onShowAnnotationsChange,
  timelineWindow,
  onTimelineWindowChange,
  selectedGroup,
  onGroupChange,
}: ECGExpandedViewProps) {
  const [localGroup, setLocalGroup] = useState<ECGAnnotationGroup | null>(null)
  const group = selectedGroup === undefined ? localGroup : selectedGroup
  const changeGroup = onGroupChange ?? setLocalGroup
  const viewerRef = useRef<ECGViewerHandle | null>(null)
  const viewerSlotRef = useRef<HTMLDivElement | null>(null)
  // La escala declarada viene del padre. Solo el estado de zoom libre queda
  // local: depende del ancho y el viewport de esta instancia del visor.
  const [onScale, setOnScale] = useState(initialViewport?.isClinicalScale ?? true)
  const [viewport, setViewport] = useState<ECGViewportChange | null>(initialViewport)
  const [viewerHeight, setViewerHeight] = useState(360)
  const [selectedAnnotationId, setSelectedAnnotationId] = useState<string | null>(
    initialSelectedAnnotationId,
  )

  // El canvas usa el espacio restante; las herramientas nunca cubren la señal.
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

  const fullSpanMs = signal.durationMs

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
  const handleResetScale = () => {
    viewerRef.current?.resetScale()
    setOnScale(true)
  }
  const handlePaperSpeedChange = (value: PaperSpeed) => {
    onPaperSpeedChange(value)
    setOnScale(true)
  }
  const handleAmplitudeChange = (value: AmplitudeMode) => {
    onAmplitudeChange(value)
    setOnScale(true)
  }
  const handleAnnotationSelect = (annotation: ECGAnnotation) => {
    if (group && !annotationIsInGroup(annotation, group)) changeGroup(null)
    focusViewerOnAnnotation(viewerRef.current, annotation)
    setSelectedAnnotationId(annotation.id)
    onAnnotationSelect?.(annotation.id)
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-3">
      <div className="flex shrink-0 flex-wrap items-center justify-between gap-3 border-b border-border pb-3">
        <Button
          variant="ghost"
          size="sm"
          onClick={onBack}
          className="-ml-2 gap-2 text-fg-muted hover:text-fg"
        >
          <ArrowLeft className="size-4" aria-hidden />
          {backLabel}
        </Button>
        <h1 className="sr-only">Visor ECG ampliado</h1>
        <ECGPaperControls
          paperSpeed={paperSpeed}
          amplitude={amplitude}
          onPaperSpeedChange={handlePaperSpeedChange}
          onAmplitudeChange={handleAmplitudeChange}
          onScale={onScale}
          onResetScale={handleResetScale}
        />
      </div>

      <ECGMinimap
        timelineWindow={timelineWindow}
        onTimelineWindowChange={onTimelineWindowChange}
        selectedGroup={group}
        onGroupSelect={changeGroup}
        signal={signal}
        viewport={viewport}
        onViewportChange={handleMinimapChange}
        selectedAnnotationId={selectedAnnotationId}
        onAnnotationSelect={handleAnnotationSelect}
        showAnnotations={showAnnotations}
      />

      <div className="grid min-h-0 flex-1 gap-4 lg:grid-cols-[minmax(0,1fr)_320px]">
        <div ref={viewerSlotRef} className="min-h-[440px] min-w-0 lg:min-h-0">
          <ECGViewer
            ref={viewerRef}
            signal={signal}
            height={viewerHeight}
            paperSpeed={paperSpeed}
            amplitude={amplitude}
            initialViewport={initialViewport ?? undefined}
            initialCursorMs={initialCursorMs ?? undefined}
            followLatest={followLatest}
            onViewportChange={handleViewportChange}
            onCursorChange={onCursorChange}
            onScaleMatchChange={setOnScale}
            selectedAnnotationId={selectedAnnotationId}
            onAnnotationSelect={handleAnnotationSelect}
            showAnnotations={showAnnotations}
            toolbar={<ECGZoomControls onZoomIn={handleZoomIn} onZoomOut={handleZoomOut} />}
          />
        </div>
        <ECGFindingsPanel
          reclassification={reclassification}
          selectedGroup={group}
          onGroupChange={changeGroup}
          annotations={signal.annotations}
          recordingStartMs={signal.startTimestamp}
          selectedAnnotationId={selectedAnnotationId}
          onAnnotationSelect={handleAnnotationSelect}
          showOnChart={showAnnotations}
          onShowOnChartChange={onShowAnnotationsChange}
          className="max-h-[28rem] min-h-64 overflow-hidden rounded-lg border border-border bg-card p-4 lg:max-h-none lg:min-h-0"
        />
      </div>
    </div>
  )
}
