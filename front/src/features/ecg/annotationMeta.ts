import {
  Activity,
  CircleAlert,
  HeartPulse,
  MessageCircleWarning,
  MessageSquareReply,
  TriangleAlert,
  type LucideIcon,
} from 'lucide-react'
import type { VariantProps } from 'class-variance-authority'

import { badgeVariants } from '@/components/ui/badge'

import type {
  ECGAnnotation,
  ECGAnnotationCategory,
  ECGAnnotationSeverity,
  ECGViewerHandle,
} from './types'

export const ANNOTATION_SEVERITY: Record<
  ECGAnnotationSeverity,
  {
    label: string
    badgeVariant: NonNullable<VariantProps<typeof badgeVariants>['variant']>
    rank: number
  }
> = {
  low: { label: 'Baja', badgeVariant: 'neutral', rank: 0 },
  medium: { label: 'Media', badgeVariant: 'info', rank: 1 },
  high: { label: 'Alta', badgeVariant: 'warning', rank: 2 },
  critical: { label: 'Crítica', badgeVariant: 'destructive', rank: 3 },
}

const KIND_LABEL: Record<string, string> = {
  tachycardia: 'Taquicardia',
  bradycardia: 'Bradicardia',
  afib: 'Fibrilación auricular',
  pvc: 'Extrasístole ventricular',
  pause: 'Pausa ventricular',
  noise: 'Ruido / artefacto',
  lead_off: 'Electrodo desconectado',
  sqi_unanalyzable: 'Señal inanalizable',
  adc_saturated: 'Saturación del ADC',
  internal_gap: 'Hueco de datos',
  symptom_marker: 'Síntoma marcado por el paciente',
  patient_report: 'Registro del paciente',
  other: 'Hallazgo',
}

const CATEGORY_ICON: Record<ECGAnnotationCategory, LucideIcon> = {
  signal_quality: TriangleAlert,
  clinical: HeartPulse,
  patient_marker: MessageCircleWarning,
  technical: Activity,
}

/**
 * Los vínculos respuesta ↔ hallazgo de una señal, resueltos una sola vez.
 *
 * Cuando el paciente contesta el formulario de un aviso, el backend ancla su
 * marca dentro de la banda del hallazgo y la manda con `linkedAnnotationId`.
 * Acá eso se convierte en los dos índices que necesitan el visor y el panel:
 * de la respuesta al hallazgo, y del hallazgo a sus respuestas.
 */
export interface ECGAnnotationLinks {
  /** id de la respuesta → hallazgo que contesta. */
  answers: ReadonlyMap<string, ECGAnnotation>
  /** id del hallazgo → respuestas del paciente. */
  responses: ReadonlyMap<string, ECGAnnotation[]>
}

export const EMPTY_ANNOTATION_LINKS: ECGAnnotationLinks = {
  answers: new Map(),
  responses: new Map(),
}

export function buildAnnotationLinks(annotations: ECGAnnotation[]): ECGAnnotationLinks {
  const byId = new Map(annotations.map((annotation) => [annotation.id, annotation]))
  const answers = new Map<string, ECGAnnotation>()
  const responses = new Map<string, ECGAnnotation[]>()
  for (const annotation of annotations) {
    const linkedId = annotation.linkedAnnotationId
    if (!linkedId) continue
    const finding = byId.get(linkedId)
    // Un vínculo que apunta a un hallazgo que no viajó en la señal no existe
    // para la UI: etiquetarlo como respuesta "de algo" sin poder decir de qué
    // confunde más que no decir nada.
    if (!finding) continue
    answers.set(annotation.id, finding)
    responses.set(finding.id, [...(responses.get(finding.id) ?? []), annotation])
  }
  return { answers, responses }
}

/** El hallazgo que este registro respondió, si viajó en la misma señal. */
export function answeredFinding(
  annotation: ECGAnnotation,
  links: ECGAnnotationLinks,
): ECGAnnotation | null {
  return links.answers.get(annotation.id) ?? null
}

/** Las respuestas del paciente a este hallazgo. */
export function annotationResponses(
  annotation: ECGAnnotation,
  links: ECGAnnotationLinks,
): ECGAnnotation[] {
  return links.responses.get(annotation.id) ?? []
}

/**
 * Seleccionar una respuesta resalta también su hallazgo, y viceversa: son dos
 * marcas superpuestas y destacar solo una deja al médico adivinando cuál de
 * las dos tocó.
 */
export function isAnnotationHighlighted(
  annotation: ECGAnnotation,
  selectedAnnotationId: string | null,
  links: ECGAnnotationLinks,
): boolean {
  if (!selectedAnnotationId) return false
  if (annotation.id === selectedAnnotationId) return true
  if (answeredFinding(annotation, links)?.id === selectedAnnotationId) return true
  return annotationResponses(annotation, links).some((item) => item.id === selectedAnnotationId)
}

/**
 * Lo que se lee sobre el gráfico. Una respuesta nombra al hallazgo que
 * contesta — es la única forma de que el médico sepa a cuál pertenece cuando
 * mira la marca sola.
 */
export function annotationChartLabel(annotation: ECGAnnotation, links: ECGAnnotationLinks): string {
  const finding = answeredFinding(annotation, links)
  if (finding) return `Respuesta: ${annotationLabel(finding.kind)}`
  return annotationLabel(annotation.kind)
}

export function annotationLabel(kind: string): string {
  const known = KIND_LABEL[kind]
  if (known) return known
  if (kind.startsWith('close_reason_')) return 'Cierre de trama informado por el Holter'
  const readable = kind.replaceAll('_', ' ').trim()
  return readable ? readable.charAt(0).toUpperCase() + readable.slice(1) : 'Hallazgo'
}

export function annotationIcon(category: ECGAnnotationCategory): LucideIcon {
  return CATEGORY_ICON[category] ?? CircleAlert
}

/** Como `annotationIcon`, pero distingue una respuesta de un registro suelto. */
export function annotationChartIcon(
  annotation: ECGAnnotation,
  links: ECGAnnotationLinks,
): LucideIcon {
  if (answeredFinding(annotation, links)) return MessageSquareReply
  return annotationIcon(annotation.category)
}

export function annotationMidpoint(annotation: ECGAnnotation): number {
  return annotation.startMs + Math.max(annotation.endMs - annotation.startMs, 0) / 2
}

export function focusViewerOnAnnotation(
  viewer: Pick<ECGViewerHandle, 'jumpTo'> | null,
  annotation: ECGAnnotation,
): void {
  viewer?.jumpTo(annotationMidpoint(annotation))
}

export function compareAnnotationsBySeverity(a: ECGAnnotation, b: ECGAnnotation): number {
  return (
    ANNOTATION_SEVERITY[b.severity].rank - ANNOTATION_SEVERITY[a.severity].rank ||
    a.startMs - b.startMs ||
    a.id.localeCompare(b.id)
  )
}

/**
 * Orden de pintado: primero las bandas, después los instantes.
 *
 * Un instante es una línea de 1 px; una banda, un relleno de todo el alto. Si
 * el orden fuera solo por severidad, la marca de una respuesta quedaría debajo
 * del hallazgo que responde —que es justo con el que se superpone— y en la
 * traza no se vería.
 */
export function compareAnnotationsForPainting(a: ECGAnnotation, b: ECGAnnotation): number {
  return (
    Number(isInstantAnnotation(a)) - Number(isInstantAnnotation(b)) ||
    ANNOTATION_SEVERITY[a.severity].rank - ANNOTATION_SEVERITY[b.severity].rank ||
    a.startMs - b.startMs ||
    a.id.localeCompare(b.id)
  )
}

/** Un momento puntual (registro del paciente) y no un intervalo. */
export function isInstantAnnotation(annotation: ECGAnnotation): boolean {
  return annotation.endMs <= annotation.startMs
}

export function formatAnnotationOffset(timestampMs: number, recordingStartMs: number): string {
  const totalSeconds = Math.max(0, Math.floor((timestampMs - recordingStartMs) / 1000))
  const hours = Math.floor(totalSeconds / 3600)
  const minutes = Math.floor((totalSeconds % 3600) / 60)
  const seconds = totalSeconds % 60
  return hours > 0
    ? `${hours}:${minutes.toString().padStart(2, '0')}:${seconds.toString().padStart(2, '0')}`
    : `${minutes}:${seconds.toString().padStart(2, '0')}`
}

export function formatAnnotationDuration(startMs: number, endMs: number): string {
  const durationMs = Math.max(0, endMs - startMs)
  if (durationMs === 0) return 'Evento puntual'
  if (durationMs < 1000) return `${Math.round(durationMs)} ms`
  return `${(durationMs / 1000).toLocaleString('es-AR', { maximumFractionDigits: 1 })} s`
}
