import { useMemo } from 'react'

import { Badge } from '@/components/ui/badge'
import { cn } from '@/lib/utils'

import {
  ANNOTATION_SEVERITY,
  annotationIcon,
  annotationLabel,
  compareAnnotationsBySeverity,
  formatAnnotationDuration,
  formatAnnotationOffset,
} from '../annotationMeta'
import type { ECGAnnotation } from '../types'

interface ECGFindingsPanelProps {
  annotations: ECGAnnotation[]
  recordingStartMs: number
  selectedAnnotationId: string | null
  onAnnotationSelect: (annotation: ECGAnnotation) => void
  className?: string
}

export function ECGFindingsPanel({
  annotations,
  recordingStartMs,
  selectedAnnotationId,
  onAnnotationSelect,
  className,
}: ECGFindingsPanelProps) {
  const sortedAnnotations = useMemo(
    () => [...annotations].sort(compareAnnotationsBySeverity),
    [annotations],
  )

  return (
    <section className={cn('flex min-h-0 flex-col gap-3', className)} aria-label="Hallazgos ECG">
      <div>
        <h2 className="text-h6 text-gray-900">Hallazgos</h2>
        <p className="text-body3 text-gray-500">
          {annotations.length === 1
            ? '1 aviso detectado'
            : `${annotations.length} avisos detectados`}
        </p>
      </div>

      {sortedAnnotations.length === 0 ? (
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
          {sortedAnnotations.map((annotation) => {
            const severity = ANNOTATION_SEVERITY[annotation.severity]
            const Icon = annotationIcon(annotation.category)
            const isSelected = annotation.id === selectedAnnotationId
            return (
              <button
                key={annotation.id}
                type="button"
                aria-pressed={isSelected}
                onClick={() => onAnnotationSelect(annotation)}
                className={cn(
                  'w-full cursor-pointer rounded-md border p-3 text-left transition-colors',
                  'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/40',
                  isSelected
                    ? 'border-primary-300 bg-primary-50'
                    : 'border-border bg-card hover:bg-gray-50',
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
                        {annotationLabel(annotation.kind)}
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
                  </span>
                </div>
              </button>
            )
          })}
        </div>
      )}
    </section>
  )
}
