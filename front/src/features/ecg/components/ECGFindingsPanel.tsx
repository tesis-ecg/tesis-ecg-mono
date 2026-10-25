import { Eye, EyeOff, MessageSquareReply, Pencil } from 'lucide-react'
import { useMemo, useState } from 'react'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import {
  FindingCorrectionStatus,
  FindingReclassifyDialog,
} from '@/features/studies/components/FindingReclassifyDialog'
import { isReclassifiable } from '@/features/studies/findingReclassification'
import type { StudyFindings } from '@/features/studies/types'
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
import { formatTimelineTime } from '../timeline'
import type { ECGAnnotation, ECGAnnotationGroup } from '../types'
import { useEcgFindingGroup } from '../hooks/useEcgFindingGroup'

function ignoreGroupChange() {}

interface ECGFindingsPanelProps {
  reclassification?: { studyId: string; findings: StudyFindings | undefined }
  annotations: ECGAnnotation[]
  recordingStartMs: number
  selectedAnnotationId: string | null
  onAnnotationSelect: (annotation: ECGAnnotation) => void
  /** Si los avisos se dibujan sobre el gráfico. Sin el callback no hay botón. */
  showOnChart?: boolean
  onShowOnChartChange?: (show: boolean) => void
  className?: string
  selectedGroup?: ECGAnnotationGroup | null
  onGroupChange?: (group: ECGAnnotationGroup | null) => void
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
  reclassification,
  annotations,
  recordingStartMs,
  selectedAnnotationId,
  onAnnotationSelect,
  showOnChart = true,
  onShowOnChartChange,
  className,
  selectedGroup = null,
  onGroupChange = ignoreGroupChange,
}: ECGFindingsPanelProps) {
  const [reclassifyingId, setReclassifyingId] = useState<string | null>(null)
  const studyFindings = reclassification?.findings
  const findingsById = useMemo(
    () =>
      new Map(
        [
          ...(studyFindings?.groups ?? []).flatMap((group) => group.items ?? []),
          ...(studyFindings?.ungrouped ?? []),
        ].map((finding) => [finding.id, finding]),
      ),
    [studyFindings],
  )
  const reclassifying = reclassifyingId ? findingsById.get(reclassifyingId) : undefined
  const group = useEcgFindingGroup(annotations, selectedGroup, onGroupChange)
  const groupIds = useMemo(() => (group ? new Set(group.annotationIds) : null), [group])
  const links = useMemo(() => buildAnnotationLinks(annotations), [annotations])
  // Solo lo que el sistema detectó (más los registros espontáneos, que tampoco
  // cuelgan de nada). Las respuestas se dibujan dentro de su hallazgo.
  const findings = useMemo(
    () =>
      annotations
        .filter((annotation) => answeredFinding(annotation, links) === null)
        .filter((annotation) => !groupIds || groupIds.has(annotation.id))
        .sort(
          groupIds
            ? (a, b) => a.startMs - b.startMs || a.id.localeCompare(b.id)
            : compareAnnotationsBySeverity,
        ),
    [annotations, links, groupIds],
  )

  return (
    <section
      className={cn('flex min-h-0 min-w-0 flex-col gap-3', className)}
      aria-label="Hallazgos ECG"
    >
      <div className="flex items-start justify-between gap-2">
        <div>
          <h2 className="text-h6 text-gray-900">Hallazgos</h2>
          <p className="text-body3 text-gray-500">
            {findings.length === 1 ? '1 aviso detectado' : `${findings.length} avisos detectados`}
          </p>
        </div>
        {/* Con muchos avisos juntos las marcas tapan la traza; ocultarlas del
            gráfico no los descarta: siguen listados acá. */}
        {onShowOnChartChange && annotations.length > 0 && (
          <Button
            type="button"
            variant="outline"
            size="sm"
            aria-pressed={!showOnChart}
            onClick={() => onShowOnChartChange(!showOnChart)}
          >
            {showOnChart ? <EyeOff aria-hidden /> : <Eye aria-hidden />}
            {showOnChart ? 'Ocultar del gráfico' : 'Mostrar en gráfico'}
          </Button>
        )}
      </div>

      {group && (
        <div
          className="flex shrink-0 flex-col gap-1 border-b border-border pb-3"
          aria-label="Grupo de hallazgos seleccionado"
        >
          <p className="text-body2 font-medium text-gray-900">{annotationLabel(group.kind)}</p>
          <p className="text-body3 tabular-nums text-gray-600">
            {formatTimelineTime(group.startMs, true)} – {formatTimelineTime(group.endMs, true)}
          </p>
          <Button variant="ghost" size="sm" className="w-fit" onClick={() => onGroupChange(null)}>
            Ver todos
          </Button>
        </div>
      )}

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
            const finding = findingsById.get(annotation.id)
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
                  'min-w-0 rounded-md border bg-card p-1 wrap-anywhere transition-colors',
                  isHighlighted ? 'border-primary-300' : 'border-border',
                )}
              >
                <div className="flex items-start gap-1">
                  <button
                    type="button"
                    aria-pressed={isSelected}
                    onClick={() => onAnnotationSelect(annotation)}
                    className={cn(
                      'min-w-0 flex-1 cursor-pointer rounded-sm p-2 text-left transition-colors',
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
                        <span className="text-body2 block font-medium text-gray-900">
                          {annotationChartLabel(annotation, links)}
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
                        {finding && <FindingCorrectionStatus finding={finding} />}
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
                  <div className="flex shrink-0 flex-col items-end gap-1 p-2 pl-0">
                    <Badge variant={severity.badgeVariant}>{severity.label}</Badge>
                    {finding && isReclassifiable(finding) && reclassification && (
                      <Button
                        type="button"
                        variant="ghost"
                        size="icon"
                        className="size-8 shrink-0"
                        aria-label={`Reclasificar ${annotationLabel(finding.kind)} del ${formatTimelineTime(finding.startEpochMs, true)}`}
                        title="Reclasificar hallazgo"
                        onClick={() => setReclassifyingId(finding.id)}
                      >
                        <Pencil className="size-3.5" aria-hidden />
                      </Button>
                    )}
                  </div>
                </div>

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
      {reclassifying && reclassification && (
        <FindingReclassifyDialog
          key={reclassifying.id}
          studyId={reclassification.studyId}
          finding={reclassifying}
          open
          onOpenChange={(open) => {
            if (!open) setReclassifyingId(null)
          }}
        />
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
