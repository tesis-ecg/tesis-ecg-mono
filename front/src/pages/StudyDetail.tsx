import { Activity, ArrowLeft, FileSearch, HeartPulse, NotebookPen } from 'lucide-react'
import { useRef, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'

import { EmptyState } from '@/components/EmptyState'
import { Spinner } from '@/components/Spinner'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { focusViewerOnAnnotation } from '@/features/ecg/annotationMeta'
import { ECGFindingsPanel } from '@/features/ecg/components/ECGFindingsPanel'
import { ECGFullscreenDialog } from '@/features/ecg/components/ECGFullscreenDialog'
import { ECGMinimap } from '@/features/ecg/components/ECGMinimap'
import { ECGPaperControls } from '@/features/ecg/components/ECGPaperControls'
import { ECGPrintableReport } from '@/features/ecg/components/ECGPrintableReport'
import { ECGViewer } from '@/features/ecg/components/ECGViewer'
import { ECGZoomControls } from '@/features/ecg/components/ECGZoomControls'
import { useEcgSignal } from '@/features/ecg/hooks/useEcgSignal'
import { usePaperScale } from '@/features/ecg/hooks/usePaperScale'
import type { ECGAnnotation, ECGViewerHandle, ECGViewportChange } from '@/features/ecg/types'
import { PatientReportsTable } from '@/features/studies/components/PatientReportsTable'
import { StudyBreadcrumb } from '@/features/studies/components/StudyBreadcrumb'
import { StudyDeviceTab } from '@/features/studies/components/StudyDeviceTab'
import { StudyHeader } from '@/features/studies/components/StudyHeader'
import { useStudy } from '@/features/studies/hooks/useStudy'
import { useStudyPatientReports } from '@/features/studies/hooks/useStudyPatientReports'
import type { StudyPatientReport } from '@/features/studies/types'
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

  const reportsQ = useStudyPatientReports(id, isInProgress)

  const viewerRef = useRef<ECGViewerHandle | null>(null)
  const [viewport, setViewport] = useState<ECGViewportChange | null>(null)
  const [fullscreenOpen, setFullscreenOpen] = useState(false)
  const [printOpen, setPrintOpen] = useState(false)
  const [selectedAnnotationId, setSelectedAnnotationId] = useState<string | null>(null)
  // La calibración vive acá y no adentro del visor: la comparten el gráfico de
  // la solapa, el de pantalla completa y el informe imprimible.
  const scale = usePaperScale()
  const [tab, setTab] = useState<'senal' | 'registros' | 'dispositivo'>('senal')

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
    // `durationMs` y no `samples.length / sampleRate`: el visor clampea contra el
    // primero, y con huecos en la grabación los dos números difieren — el botón
    // se quedaba corto y no llegaba al final del estudio.
    const fullSpan = ecgQ.data.durationMs
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
  const handleResetScale = () => {
    viewerRef.current?.resetScale()
    scale.setOnScale(true)
  }
  const handleAnnotationSelect = (annotation: ECGAnnotation) => {
    if (!ecgQ.data) return
    focusViewerOnAnnotation(viewerRef.current, annotation)
    setSelectedAnnotationId(annotation.id)
  }
  /**
   * El registro y su banda comparten el id, así que alcanza con volver a la
   * solapa de la señal y buscar la anotación correspondiente. Si todavía no
   * está (el lote llegó entre un fetch y el otro), la fila no ofrece el botón.
   */
  const handleLocateReport = (report: StudyPatientReport) => {
    const annotation = ecgQ.data?.annotations.find((item) => item.id === report.id)
    if (!annotation) return
    setTab('senal')
    setSelectedAnnotationId(annotation.id)
    // El viewer de la solapa recién se monta en el próximo frame.
    window.requestAnimationFrame(() => focusViewerOnAnnotation(viewerRef.current, annotation))
  }

  return (
    <div className="flex flex-col gap-4">
      <StudyBreadcrumb
        patientId={study.patientId}
        patientName={study.patientName}
        startedAt={study.startedAt}
      />
      <StudyHeader study={study} />

      <Tabs value={tab} onValueChange={(next) => setTab(next as typeof tab)}>
        <TabsList>
          <TabsTrigger value="senal">
            <Activity className="size-4" aria-hidden />
            Señal ECG
          </TabsTrigger>
          <TabsTrigger value="registros">
            <NotebookPen className="size-4" aria-hidden />
            Registros del paciente
            {reportsQ.data && reportsQ.data.total > 0 && (
              <span className="text-body3 rounded-full bg-primary-50 px-1.5 text-primary-500">
                {reportsQ.data.total}
              </span>
            )}
          </TabsTrigger>
          {study.canAccessDevice && (
            <TabsTrigger value="dispositivo">
              <HeartPulse className="size-4" aria-hidden />
              Dispositivo
            </TabsTrigger>
          )}
        </TabsList>

        {/* `forceMount`: sin esto Radix desmonta el contenido inactivo y el
            viewer de uPlot perdería el zoom, el viewport y su canvas cada vez
            que el médico pasa por la solapa de registros — y "Ver en el ECG"
            tendría que esperar a que se vuelva a montar para poder saltar. */}
        <TabsContent value="senal" forceMount className="data-[state=inactive]:hidden">
          <div className="grid grid-cols-1 items-stretch gap-4 lg:grid-cols-4">
            <Card className="flex min-h-0 flex-col gap-3 p-4 lg:col-span-3">
              {!studyHasSignal ? (
                <EmptyState
                  icon={FileSearch}
                  title={
                    study.status === 'scheduled' ? 'Estudio todavía no iniciado' : 'Sin señal ECG'
                  }
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
                  <ECGPaperControls
                    paperSpeed={scale.paperSpeed}
                    amplitude={scale.amplitude}
                    onPaperSpeedChange={scale.setPaperSpeed}
                    onAmplitudeChange={scale.setAmplitude}
                    onScale={scale.onScale}
                    onResetScale={handleResetScale}
                    onPrint={() => setPrintOpen(true)}
                  />
                  <ECGMinimap
                    signal={ecgQ.data}
                    viewport={viewport}
                    onViewportChange={handleMinimapChange}
                    selectedAnnotationId={selectedAnnotationId}
                    onAnnotationSelect={handleAnnotationSelect}
                  />
                  <ECGViewer
                    ref={viewerRef}
                    signal={ecgQ.data}
                    height={400}
                    paperSpeed={scale.paperSpeed}
                    amplitude={scale.amplitude}
                    onViewportChange={setViewport}
                    onScaleMatchChange={scale.setOnScale}
                    selectedAnnotationId={selectedAnnotationId}
                    onAnnotationSelect={handleAnnotationSelect}
                  />
                  <p className="text-body3 mt-10 text-gray-500">
                    Zoom:{' '}
                    <kbd className="rounded border border-border bg-muted px-1">
                      Ctrl/⌘ + scroll
                    </kbd>{' '}
                    · Pan: drag o flechas izq/der con focus en el gráfico
                  </p>
                </>
              ) : null}
            </Card>

            <aside className="min-h-0 lg:relative lg:col-span-1">
              <Card className="flex max-h-[32rem] min-h-0 flex-col p-4 lg:absolute lg:inset-0 lg:max-h-none">
                <ECGFindingsPanel
                  annotations={ecgQ.data?.annotations ?? []}
                  recordingStartMs={ecgQ.data?.startTimestamp ?? 0}
                  selectedAnnotationId={selectedAnnotationId}
                  onAnnotationSelect={handleAnnotationSelect}
                  className="h-full"
                />
              </Card>
            </aside>
          </div>
        </TabsContent>

        <TabsContent value="registros">
          <Card className="p-6">
            {reportsQ.isLoading ? (
              <Spinner label="Cargando registros…" />
            ) : reportsQ.isError ? (
              <EmptyState
                title="No pudimos cargar los registros"
                description={unwrapError(reportsQ.error)}
                action={
                  <Button variant="outline" onClick={() => void reportsQ.refetch()}>
                    Reintentar
                  </Button>
                }
              />
            ) : (
              <PatientReportsTable
                reports={reportsQ.data?.items ?? []}
                pendingSignalTotal={reportsQ.data?.pendingSignalTotal ?? 0}
                onLocate={handleLocateReport}
              />
            )}
          </Card>
        </TabsContent>

        {study.canAccessDevice && (
          <TabsContent value="dispositivo">
            <StudyDeviceTab deviceId={study.deviceId} />
          </TabsContent>
        )}
      </Tabs>

      {ecgQ.data && (
        <>
          <ECGFullscreenDialog
            signal={ecgQ.data}
            initialViewport={viewport}
            open={fullscreenOpen}
            onOpenChange={setFullscreenOpen}
            onClose={handleFullscreenClose}
            paperSpeed={scale.paperSpeed}
            amplitude={scale.amplitude}
            onPaperSpeedChange={scale.setPaperSpeed}
            onAmplitudeChange={scale.setAmplitude}
            selectedAnnotationId={selectedAnnotationId}
            onAnnotationSelect={setSelectedAnnotationId}
          />
          <ECGPrintableReport
            open={printOpen}
            onOpenChange={setPrintOpen}
            signal={ecgQ.data}
            viewport={viewport}
            paperSpeed={scale.paperSpeed}
            amplitude={scale.amplitude}
            patientName={study.patientName}
            studyStartedAt={study.startedAt}
          />
        </>
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
