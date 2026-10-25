import { ArrowLeft, FileSearch } from 'lucide-react'
import { useLocation, useNavigate, useParams } from 'react-router-dom'

import { EmptyState } from '@/components/EmptyState'
import { Spinner } from '@/components/Spinner'
import { Button } from '@/components/ui/button'
import { ECGExpandedView } from '@/features/ecg/components/ECGExpandedView'
import { useEcgSession } from '@/features/ecg/hooks/useEcgSession'
import { useEcgSignal } from '@/features/ecg/hooks/useEcgSignal'
import { timeVerificationMessage } from '@/features/ecg/timeVerificationBanner'
import { useStudy } from '@/features/studies/hooks/useStudy'
import { useStudyFindings } from '@/features/studies/hooks/useStudyFindings'
import { isApiError, unwrapError } from '@/lib/api'

export function StudyEcg() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const location = useLocation()
  const session = useEcgSession()
  const studyQ = useStudy(id)
  const study = studyQ.data
  const hasSignal =
    !!study && study.durationMs > 0 && study.status !== 'scheduled' && study.status !== 'cancelled'
  const isInProgress = study?.status === 'in_progress'
  const findingsQ = useStudyFindings(hasSignal ? id : undefined, isInProgress)
  const ecgQ = useEcgSignal(hasSignal ? id : undefined, isInProgress)
  const back = () => {
    if (location.state?.fromStudy === true) navigate(-1)
    else navigate(`/studies/${id}`, { replace: true })
  }
  const signal = ecgQ.data
  const timeWarning = timeVerificationMessage(
    signal?.metadata?.timeVerification ??
      (signal?.metadata?.startTimeVerified === false
        ? {
            allVerified: false,
            segmentCount: 0,
            unverifiedSegmentCount: 0,
            unverifiedDurationMs: 0,
            maxUncertaintyMs: null,
          }
        : undefined),
  )

  let content
  if (studyQ.isError) {
    const notFound = isApiError(studyQ.error) && studyQ.error.code === 'NOT_FOUND'
    content = (
      <EmptyState
        title={notFound ? 'Estudio no encontrado' : 'No pudimos cargar el estudio'}
        description={
          notFound ? 'El estudio no existe o ya no está disponible.' : unwrapError(studyQ.error)
        }
        action={
          notFound ? undefined : (
            <Button variant="outline" onClick={() => void studyQ.refetch()}>
              Reintentar
            </Button>
          )
        }
      />
    )
  } else if (studyQ.isLoading || !study) {
    content = <Spinner label="Cargando estudio…" />
  } else if (!hasSignal) {
    content = (
      <EmptyState
        icon={FileSearch}
        title={study.status === 'scheduled' ? 'Estudio todavía no iniciado' : 'Sin señal ECG'}
        description="La señal estará disponible cuando el estudio contenga muestras de ECG."
      />
    )
  } else if (ecgQ.isError) {
    content = (
      <EmptyState
        title="No pudimos cargar la señal ECG"
        description={unwrapError(ecgQ.error)}
        action={
          <Button variant="outline" onClick={() => void ecgQ.refetch()}>
            Reintentar
          </Button>
        }
      />
    )
  } else if (!signal) {
    content = (
      <Spinner
        label={isInProgress ? 'Esperando el primer lote del Holter…' : 'Cargando señal ECG…'}
      />
    )
  } else {
    return (
      <div className="flex h-full min-h-0 flex-col gap-3">
        {timeWarning && <p className="text-body3 shrink-0 text-amber-700">{timeWarning}</p>}
        <ECGExpandedView
          reclassification={{ studyId: study.id, findings: findingsQ.data }}
          timelineWindow={session.timelineWindow}
          onTimelineWindowChange={session.setTimelineWindow}
          selectedGroup={session.selectedGroup}
          onGroupChange={session.setSelectedGroup}
          signal={signal}
          initialViewport={session.viewport}
          initialCursorMs={session.cursorMs}
          onViewportChange={session.setViewport}
          onCursorChange={session.setCursorMs}
          onBack={back}
          initialSelectedAnnotationId={session.selectedAnnotationId}
          paperSpeed={session.scale.paperSpeed}
          amplitude={session.scale.amplitude}
          onPaperSpeedChange={session.scale.setPaperSpeed}
          onAmplitudeChange={session.scale.setAmplitude}
          onAnnotationSelect={session.setSelectedAnnotationId}
          showAnnotations={session.showAnnotationsOnChart}
          onShowAnnotationsChange={session.setShowAnnotationsOnChart}
          followLatest={isInProgress}
        />
      </div>
    )
  }

  return (
    <div className="flex h-full min-h-80 flex-col gap-4">
      <Button variant="ghost" size="sm" className="w-fit gap-2" onClick={back}>
        <ArrowLeft className="size-4" aria-hidden />
        Volver al estudio
      </Button>
      <div className="flex flex-1 items-center justify-center">{content}</div>
    </div>
  )
}
