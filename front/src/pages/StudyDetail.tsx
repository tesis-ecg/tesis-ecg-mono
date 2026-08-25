import { ArrowLeft, FileSearch } from 'lucide-react'
import { useRef, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'

import { EmptyState } from '@/components/EmptyState'
import { Spinner } from '@/components/Spinner'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { ECGFullscreenDialog } from '@/features/ecg/components/ECGFullscreenDialog'
import { ECGMinimap } from '@/features/ecg/components/ECGMinimap'
import { ECGViewer } from '@/features/ecg/components/ECGViewer'
import { ECGZoomControls } from '@/features/ecg/components/ECGZoomControls'
import { useEcgSignal } from '@/features/ecg/hooks/useEcgSignal'
import type { ECGViewerHandle, ECGViewportChange } from '@/features/ecg/types'
import { StudyBreadcrumb } from '@/features/studies/components/StudyBreadcrumb'
import { StudyHeader } from '@/features/studies/components/StudyHeader'
import { useStudy } from '@/features/studies/hooks/useStudy'
import { isApiError, unwrapError } from '@/lib/api'

export function StudyDetail() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()

  const studyQ = useStudy(id)
  const studyHasSignal =
    studyQ.data !== undefined &&
    studyQ.data.durationMs > 0 &&
    studyQ.data.status !== 'scheduled' &&
    studyQ.data.status !== 'cancelled'
  // Un estudio en curso sigue recibiendo lotes del chaleco: la señal crece
  // mientras el médico la mira, así que el hook la vuelve a pedir cada tanto.
  const isInProgress = studyQ.data?.status === 'in_progress'
  const ecgQ = useEcgSignal(studyHasSignal ? id : undefined, isInProgress)

  const viewerRef = useRef<ECGViewerHandle | null>(null)
  const [viewport, setViewport] = useState<ECGViewportChange | null>(null)
  const [fullscreenOpen, setFullscreenOpen] = useState(false)

  // 404 → estado dedicado.
  if (studyQ.isError && isApiError(studyQ.error) && studyQ.error.code === 'NOT_FOUND') {
    return (
      <div className="flex flex-col gap-4">
        <BackToList navigate={navigate} />
        <Card className="p-6">
          <EmptyState
            icon={FileSearch}
            title="Estudio no encontrado"
            description="El estudio que estás buscando no existe o ya no está disponible."
            action={
              <Button variant="outline" onClick={() => navigate('/studies')}>
                <ArrowLeft className="mr-2 size-4" aria-hidden />
                Volver al listado de estudios
              </Button>
            }
          />
        </Card>
      </div>
    )
  }

  if (studyQ.isError) {
    return (
      <div className="flex flex-col gap-4">
        <BackToList navigate={navigate} />
        <Card className="p-6">
          <EmptyState
            title="No pudimos cargar el estudio"
            description={unwrapError(studyQ.error)}
            action={
              <Button variant="outline" onClick={() => void studyQ.refetch()}>
                Reintentar
              </Button>
            }
          />
        </Card>
      </div>
    )
  }

  if (studyQ.isLoading || !studyQ.data) {
    return (
      <div className="flex flex-col gap-4">
        <BackToList navigate={navigate} />
        <Card className="p-6">
          <Skeleton className="h-6 w-72" />
        </Card>
        <Card className="p-6">
          <Spinner label="Cargando estudio…" />
        </Card>
      </div>
    )
  }

  const study = studyQ.data

  const handleZoomIn = () => {
    if (!viewport) return
    const span = viewport.endMs - viewport.startMs
    const center = (viewport.startMs + viewport.endMs) / 2
    const newSpan = Math.max(500, span / 2)
    viewerRef.current?.zoomToRange(center - newSpan / 2, center + newSpan / 2)
  }
  const handleZoomOut = () => {
    if (!viewport || !ecgQ.data) return
    const span = viewport.endMs - viewport.startMs
    const center = (viewport.startMs + viewport.endMs) / 2
    const fullSpan = (ecgQ.data.samples.length / ecgQ.data.sampleRate) * 1000
    const newSpan = Math.min(fullSpan, span * 2)
    viewerRef.current?.zoomToRange(center - newSpan / 2, center + newSpan / 2)
  }
  const handleFullscreen = () => setFullscreenOpen(true)
  const handleFullscreenClose = (lastViewport: ECGViewportChange | null) => {
    if (lastViewport) {
      viewerRef.current?.zoomToRange(lastViewport.startMs, lastViewport.endMs)
    }
  }
  const handleMinimapChange = (next: ECGViewportChange) => {
    viewerRef.current?.zoomToRange(next.startMs, next.endMs)
  }

  return (
    <div className="flex flex-col gap-4">
      <StudyBreadcrumb
        patientId={study.patientId}
        patientName={study.patientName}
        startedAt={study.startedAt}
      />
      <StudyHeader study={study} />

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-4">
        <Card className="flex flex-col gap-3 p-4 lg:col-span-3">
          {!studyHasSignal ? (
            <EmptyState
              icon={FileSearch}
              title={study.status === 'scheduled' ? 'Estudio todavía no iniciado' : 'Sin señal ECG'}
              description={
                study.status === 'scheduled'
                  ? 'La señal estará disponible cuando comience el estudio y el Holter envíe sus primeras muestras.'
                  : 'Este estudio no contiene muestras de ECG para visualizar.'
              }
            />
          ) : ecgQ.isLoading && isInProgress ? (
            <div className="flex h-[480px] items-center justify-center">
              <Spinner label="Esperando el primer lote del Holter…" />
            </div>
          ) : ecgQ.isLoading ? (
            <div className="flex h-[480px] items-center justify-center">
              <Spinner label="Cargando señal ECG…" />
            </div>
          ) : ecgQ.isError ? (
            <EmptyState
              title="No pudimos cargar la señal ECG"
              description={unwrapError(ecgQ.error)}
              action={
                <Button variant="outline" onClick={() => void ecgQ.refetch()}>
                  Reintentar
                </Button>
              }
            />
          ) : ecgQ.data ? (
            <>
              <div className="flex items-center justify-between gap-2">
                <div className="flex items-center gap-2">
                  <h2 className="text-h6 text-gray-900">Señal ECG</h2>
                  {isInProgress && (
                    <span className="flex items-center gap-1.5 rounded-full bg-primary-50 px-2 py-0.5 text-body3 text-primary-500">
                      <span className="size-1.5 animate-pulse rounded-full bg-primary-500" />
                      Recibiendo datos
                      {ecgQ.isFetching && ' · actualizando'}
                    </span>
                  )}
                </div>
                <ECGZoomControls
                  onZoomIn={handleZoomIn}
                  onZoomOut={handleZoomOut}
                  onFullscreen={handleFullscreen}
                />
              </div>
              <ECGMinimap
                signal={ecgQ.data}
                viewport={viewport}
                onViewportChange={handleMinimapChange}
              />
              <ECGViewer
                ref={viewerRef}
                signal={ecgQ.data}
                height={400}
                onViewportChange={setViewport}
              />
              <p className="text-body3 mt-10 text-gray-500">
                Zoom:{' '}
                <kbd className="rounded border border-border bg-muted px-1">Ctrl/⌘ + scroll</kbd> ·
                Pan: drag o flechas izq/der con focus en el gráfico
              </p>
            </>
          ) : null}
        </Card>

        <aside className="lg:col-span-1">
          <Card className="h-full p-4">
            <EmptyState
              icon={FileSearch}
              title="Hallazgos"
              description="La detección automática de eventos todavía no está disponible. Por ahora los hallazgos se revisan sobre la señal."
            />
          </Card>
        </aside>
      </div>

      {ecgQ.data && (
        <ECGFullscreenDialog
          signal={ecgQ.data}
          initialViewport={viewport}
          open={fullscreenOpen}
          onOpenChange={setFullscreenOpen}
          onClose={handleFullscreenClose}
        />
      )}
    </div>
  )
}

/**
 * A un estudio se llega desde /studies, desde la ficha del paciente o desde el
 * dispositivo. Mandar siempre a /patients descartaba el contexto del usuario;
 * `navigate(-1)` lo devuelve de donde vino, con /studies como red de seguridad
 * cuando entró por URL directa y no hay historia propia.
 */
function BackToList({ navigate }: { navigate: ReturnType<typeof useNavigate> }) {
  const handleBack = () => {
    if (window.history.length > 1) navigate(-1)
    else navigate('/studies')
  }

  return (
    <Button
      variant="ghost"
      size="sm"
      onClick={handleBack}
      className="w-fit text-gray-600 hover:text-primary-500"
    >
      <ArrowLeft className="mr-1 size-4" aria-hidden />
      Volver
    </Button>
  )
}
