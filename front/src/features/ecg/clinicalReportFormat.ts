import { CLINICAL_TIME_ZONE } from '@/lib/time'

/** Geometría del informe: A4 vertical, en milímetros. */
export const PAGE_WIDTH = 210
export const PAGE_HEIGHT = 297
export const MARGIN = 12
export const CONTENT_WIDTH = PAGE_WIDTH - MARGIN * 2
export const INK = [18, 46, 92] as const
export const GRID = [238, 198, 198] as const
export const BOX_FILL = [236, 241, 248] as const

/** Lo que se imprime donde una métrica no se pudo calcular. */
export const NOT_AVAILABLE = 'N/D'

export function formatDate(value: string | number): string {
  return new Intl.DateTimeFormat('es-AR', {
    dateStyle: 'short',
    timeStyle: 'medium',
    timeZone: CLINICAL_TIME_ZONE,
    hour12: false,
  }).format(new Date(value))
}

export function formatAxisTime(value: number): string {
  return new Intl.DateTimeFormat('es-AR', {
    timeZone: CLINICAL_TIME_ZONE,
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  }).format(new Date(value))
}

export function formatHour(value: number): string {
  return new Intl.DateTimeFormat('es-AR', {
    timeZone: CLINICAL_TIME_ZONE,
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).format(new Date(value))
}

export function formatCalendarDate(value: string): string {
  const [year, month, day] = value.split('-').map(Number)
  return new Intl.DateTimeFormat('es-AR', {
    dateStyle: 'short',
    timeZone: CLINICAL_TIME_ZONE,
  }).format(new Date(Date.UTC(year, month - 1, day, 12)))
}

export function formatDuration(ms: number): string {
  if (ms > 0 && ms < 1000) return `${Math.round(ms)} ms`
  const seconds = Math.max(0, Math.round(ms / 1000))
  const hours = Math.floor(seconds / 3600)
  const minutes = Math.floor((seconds % 3600) / 60)
  const remaining = seconds % 60
  return `${hours} h ${minutes} min ${remaining} s`
}

/**
 * Un valor numérico con la coma decimal de es-AR, o `N/D`.
 * `null` y `undefined` significan "no se pudo calcular", nunca cero.
 */
export function formatMetricValue(
  value: number | null | undefined,
  digits = 0,
  unit?: string,
): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return NOT_AVAILABLE
  const text = new Intl.NumberFormat('es-AR', {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  }).format(value)
  return unit ? `${text} ${unit}` : text
}

export function sexLabel(sex: string | null | undefined): string {
  return { M: 'Masculino', F: 'Femenino', X: 'No binario' }[sex ?? ''] ?? (sex || '—')
}

export function age(birthDate: string): number {
  const birth = new Date(birthDate)
  const now = new Date()
  let value = now.getFullYear() - birth.getFullYear()
  if (
    now.getMonth() < birth.getMonth() ||
    (now.getMonth() === birth.getMonth() && now.getDate() < birth.getDate())
  ) {
    value--
  }
  return Math.max(0, value)
}

export function clamp(value: number, min: number, max: number) {
  return Math.min(max, Math.max(min, value))
}
