import { formatDurationMs } from '@/lib/time'

import type { ECGTimeVerification } from './types'

const LEGACY_MESSAGE = 'Hora de pared no verificada: faltó un ancla del mismo arranque del Holter.'

function formatUncertainty(ms: number): string {
  if (ms < 60_000) {
    return `${(ms / 1000).toLocaleString('es-AR', { maximumFractionDigits: 1 })} s`
  }
  return formatDurationMs(ms)
}

/**
 * Texto del aviso de hora de pared, o `null` si no hay nada que avisar.
 * Sin tramos (estudio legacy) conserva el mensaje global; con tramos, dice
 * cuántos quedaron sin verificar y cuánto tiempo suman.
 */
export function timeVerificationMessage(
  verification: ECGTimeVerification | undefined,
): string | null {
  if (!verification || verification.allVerified) return null
  if (verification.segmentCount === 0) return LEGACY_MESSAGE

  const { segmentCount, unverifiedSegmentCount, unverifiedDurationMs, maxUncertaintyMs } =
    verification
  const noun = segmentCount === 1 ? 'tramo' : 'tramos'
  const estimate =
    maxUncertaintyMs != null
      ? `Su hora es estimada (±${formatUncertainty(maxUncertaintyMs)})`
      : 'Su hora es estimada'
  return (
    `${unverifiedSegmentCount} de ${segmentCount} ${noun} sin hora verificada ` +
    `(${formatDurationMs(unverifiedDurationMs)}). ${estimate} y se marca en el eje.`
  )
}
