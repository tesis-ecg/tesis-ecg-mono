import type { ActivityPoint, ActivityTrend, AlertSeverity } from './types'

/**
 * Lo que los gráficos de la home calculan antes de dibujar.
 *
 * Vive aparte de los componentes a propósito: un porcentaje mal calculado o una
 * severidad pintada del color equivocado no rompen el render, así que no hay
 * forma de que salten mirando la pantalla. Acá se pueden testear.
 */

export interface TrendReading {
  /** Diferencia absoluta contra el período anterior. */
  delta: number
  /** Variación porcentual, o `null` cuando no hay base contra la cual medir. */
  percent: number | null
  direction: 'up' | 'down' | 'flat'
}

export function readTrend(trend: ActivityTrend): TrendReading {
  const delta = trend.current - trend.previous
  return {
    delta,
    // Sin período anterior no hay porcentaje que valga: cualquier número contra
    // cero da infinito y "+∞%" en una tarjeta clínica no dice nada. El chip cae
    // en el valor absoluto.
    percent: trend.previous === 0 ? null : (delta / trend.previous) * 100,
    direction: delta > 0 ? 'up' : delta < 0 ? 'down' : 'flat',
  }
}

/**
 * Cómo se lee una variación.
 *
 * No es universal: que suban las alertas es malo y que suban los estudios es
 * bueno, así que el color lo decide quien usa el chip, no el signo.
 */
export type TrendPolarity = 'more-is-good' | 'more-is-bad' | 'neutral'

export function trendTone(
  direction: TrendReading['direction'],
  polarity: TrendPolarity,
): 'positive' | 'negative' | 'neutral' {
  if (direction === 'flat' || polarity === 'neutral') return 'neutral'
  const isGood = polarity === 'more-is-good' ? direction === 'up' : direction === 'down'
  return isGood ? 'positive' : 'negative'
}

export function formatTrend(reading: TrendReading): string {
  const sign = reading.delta > 0 ? '+' : ''
  if (reading.percent === null) return `${sign}${reading.delta}`
  return `${sign}${reading.percent.toFixed(1)}%`
}

/**
 * Los colores de severidad son los mismos que pinta el visor de ECG
 * (`--ecg-alert-*` en `styles/tokens.css`).
 *
 * Recharts pinta sobre SVG con `fill`, así que toma la variable CSS directo. Que
 * sea la misma escala del gráfico de la traza no es cosmético: el médico que ve
 * una banda roja en el ECG tiene que reconocer ese rojo en el donut de la home.
 */
export const SEVERITY_META: Record<AlertSeverity, { label: string; color: string }> = {
  critical: { label: 'Crítica', color: 'var(--ecg-alert-critical)' },
  high: { label: 'Alta', color: 'var(--ecg-alert-high)' },
  medium: { label: 'Media', color: 'var(--ecg-alert-medium)' },
  low: { label: 'Baja', color: 'var(--ecg-alert-low)' },
}

/** Porcentaje de la flota que está transmitiendo. Sin chalecos, no hay dato. */
export function fleetPercent(assigned: number, transmitting: number): number | null {
  if (assigned <= 0) return null
  return Math.round((transmitting / assigned) * 100)
}

const WEEKDAY = new Intl.DateTimeFormat('es-AR', { weekday: 'short' })

/**
 * `2026-09-04` → `jue`.
 *
 * La fecha se parte a mano: un `YYYY-MM-DD` lo parsea `Date` como medianoche
 * **UTC**, y en Argentina eso corre la etiqueta un día para atrás — el gráfico
 * decía "miércoles" arriba de las barras del jueves.
 */
export function weekdayLabel(isoDate: string): string {
  const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(isoDate)
  if (!match) return ''
  const [, year, month, day] = match.map(Number)
  const label = WEEKDAY.format(new Date(year, month - 1, day))
  return label.replace('.', '')
}

/** El último punto de la serie es hoy: es el que el gráfico destaca. */
export function isToday(isoDate: string, days: ActivityPoint[]): boolean {
  return days.length > 0 && days[days.length - 1].date === isoDate
}
