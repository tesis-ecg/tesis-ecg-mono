import {
  Activity,
  CircleAlert,
  HeartPulse,
  MessageCircleWarning,
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
  other: 'Hallazgo',
}

const CATEGORY_ICON: Record<ECGAnnotationCategory, LucideIcon> = {
  signal_quality: TriangleAlert,
  clinical: HeartPulse,
  patient_marker: MessageCircleWarning,
  technical: Activity,
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

export function compareAnnotationsForPainting(a: ECGAnnotation, b: ECGAnnotation): number {
  return (
    ANNOTATION_SEVERITY[a.severity].rank - ANNOTATION_SEVERITY[b.severity].rank ||
    a.startMs - b.startMs ||
    a.id.localeCompare(b.id)
  )
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
