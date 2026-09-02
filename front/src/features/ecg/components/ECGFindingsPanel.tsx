import { MessageSquareReply } from 'lucide-react'
import { useMemo } from 'react'

import { Badge } from '@/components/ui/badge'
import { cn } from '@/lib/utils'

import {
  ANNOTATION_SEVERITY,
  annotationChartIcon,
  annotationChartLabel,
  annotationLabel,
  annotationResponses,
  answeredFinding,
  buildAnnotationLinks,
  compareAnnotationsBySeverity,
  formatAnnotationDuration,
  formatAnnotationOffset,
  isAnnotationHighlighted,
} from '../annotationMeta'
import type { ECGAnnotation } from '../types'

interface ECGFindingsPanelProps {
  annotations: ECGAnnotation[]
  recordingStartMs: number
  selectedAnnotationId: string | null
  onAnnotationSelect: (annotation: ECGAnnotation) => void
  className?: string
}

/**
 * Lista de avisos de la señal.
 *
 * La respuesta del paciente **no es un aviso más**: es la contestación al
 * formulario de uno de ellos. Listarla al mismo nivel inflaba la cuenta de
 * "avisos detectados" con algo que el sistema no detectó, y dejaba al médico
 * emparejando dos tarjetas separadas. Acá cuelga de la tarjeta del hallazgo que
 * contesta, sangrada bajo su misma línea, y sigue siendo clickeable para saltar
 * a su marca sobre la traza.
 */
export function ECGFindingsPanel({
  annotations,
  recordingStartMs,
  selectedAnnotationId,
  onAnnotationSelect,
  className,
}: ECGFindingsPanelProps) {
  const links = useMemo(() => buildAnnotationLinks(annotations), [annotations])
  // Solo lo que el sistema detectó (más los registros espontáneos, que tampoco
  // cuelgan de nada). Las respuestas se dibujan dentro de su hallazgo.
  const findings = useMemo(
    () =>
      annotations
        .filter((annotation) => answeredFinding(annotation, links) === null)
        .sort(compareAnnotationsBySeverity),
    [annotations, links],
  )

  return (
    <section className={cn('flex min-h-0 flex-col gap-3', className)} aria-label="Hallazgos ECG">
      <div>
        <h2 className="text-h6 text-gray-900">Hallazgos</h2>
        <p className="text-body3 text-gray-500">
          {findings.length === 1 ? '1 aviso detectado' : `${findings.length} avisos detectados`}
        </p>
      </div>

      {findings.length === 0 ? (
        <div className="flex flex-1 items-center justify-center rounded-md border border-dashed border-border p-5 text-center">
          <div>
            <p className="text-body2 font-medium text-gray-800">Sin avisos detectados</p>
            <p className="text-body3 mt-1 text-gray-500">
              No hay problemas de calidad ni hallazgos asociados a esta señal.
            </p>
          </div>
        </div>
      ) : (
        <div className="min-h-0 space-y-2 overflow-y-auto pr-1">
          {findings.map((annotation) => {
            const severity = ANNOTATION_SEVERITY[annotation.severity]
            const Icon = annotationChartIcon(annotation, links)
            const isSelected = annotation.id === selectedAnnotationId
            // Resaltado también cuando el seleccionado es su respuesta: la
            // tarjeta entera es la unidad "hallazgo + lo que contestó".
            const isHighlighted = isAnnotationHighlighted(annotation, selectedAnnotationId, links)
            const responses = annotationResponses(annotation, links)
            return (
              <div
                key={annotation.id}
                data-annotation-card={annotation.id}
                className={cn(
                  'rounded-md border bg-card p-1 transition-colors',
                  isHighlighted ? 'border-primary-300' : 'border-border',
                )}
              >
                <button
                  type="button"
                  aria-pressed={isSelected}
                  onClick={() => onAnnotationSelect(annotation)}
                  className={cn(
                    'w-full cursor-pointer rounded-sm p-2 text-left transition-colors',
                    'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/40',
                    isSelected ? 'bg-primary-50' : 'hover:bg-gray-50',
                  )}
                >
                  <div className="flex items-start gap-2.5">
                    <span
                      className="mt-0.5 flex size-7 shrink-0 items-center justify-center rounded-full"
                      style={{
                        color: `var(--ecg-alert-${annotation.severity})`,
                        backgroundColor: `var(--ecg-alert-${annotation.severity}-bg)`,
                      }}
                    >
                      <Icon className="size-4" aria-hidden />
                    </span>
                    <span className="min-w-0 flex-1">
                      <span className="flex flex-wrap items-center justify-between gap-1">
                        <span className="text-body2 font-medium text-gray-900">
                          {annotationChartLabel(annotation, links)}
                        </span>
                        <Badge variant={severity.badgeVariant}>{severity.label}</Badge>
                      </span>
                      <span className="text-body3 mt-1 block text-gray-600">
                        {formatAnnotationOffset(annotation.startMs, recordingStartMs)} ·{' '}
                        {formatAnnotationDuration(annotation.startMs, annotation.endMs)}
                      </span>
                      {annotation.confidenceScore != null && (
                        <span className="text-body3 mt-0.5 block text-gray-500">
                          Confianza {Math.round(annotation.confidenceScore * 100)}%
                        </span>
                      )}
                      {/* Un registro espontáneo trae sus síntomas acá mismo; los
                          de una respuesta viven en su propio bloque, abajo. */}
                      {annotation.description && (
                        <span className="text-body3 mt-1 block text-gray-800">
                          {annotation.description}
                        </span>
                      )}
                    </span>
                  </div>
                </button>

                {responses.map((response) => (
                  <PatientResponse
                    key={response.id}
                    response={response}
                    finding={annotation}
                    recordingStartMs={recordingStartMs}
                    isSelected={response.id === selectedAnnotationId}
                    onSelect={onAnnotationSelect}
                  />
                ))}
              </div>
            )
          })}
        </div>
      )}
    </section>
  )
}

interface PatientResponseProps {
  response: ECGAnnotation
  finding: ECGAnnotation
  recordingStartMs: number
  isSelected: boolean
  onSelect: (annotation: ECGAnnotation) => void
}

/**
 * La contestación del paciente, colgada del aviso que responde.
 *
 * Va sangrada y con una guía vertical —la misma convención que una respuesta en
 * un hilo— para que se lea como parte del hallazgo y no como otro aviso. Es un
 * botón propio y no texto: sobre el ECG tiene su propia marca dentro de la
 * banda, y el médico tiene que poder saltar ahí.
 */
function PatientResponse({
  response,
  finding,
  recordingStartMs,
  isSelected,
  onSelect,
}: PatientResponseProps) {
  return (
    <div className="ml-5 border-l-2 border-primary-100 pl-2.5">
      <button
        type="button"
        aria-pressed={isSelected}
        aria-label={`Respuesta del paciente al aviso de ${annotationLabel(
          finding.kind,
        ).toLowerCase()}`}
        onClick={() => onSelect(response)}
        className={cn(
          'w-full cursor-pointer rounded-sm p-2 text-left transition-colors',
          'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/40',
          isSelected ? 'bg-primary-50' : 'hover:bg-gray-50',
        )}
      >
        <span className="text-body3 flex items-center gap-1.5 font-medium text-primary-600">
          <MessageSquareReply className="size-3.5 shrink-0" aria-hidden />
          Respuesta del paciente
        </span>
        <span className="text-body3 mt-0.5 block text-gray-800">
          {response.description ?? 'No informó síntomas'}
        </span>
        <span className="text-helper mt-0.5 block text-gray-500">
          {formatAnnotationOffset(response.startMs, recordingStartMs)} · marcada sobre el hallazgo
        </span>
      </button>
    </div>
  )
}
