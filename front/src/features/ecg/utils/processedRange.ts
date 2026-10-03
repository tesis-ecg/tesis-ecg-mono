import type { ECGSignal } from '../types'

/**
 * Hora de pared donde arranca el tramo final sin procesar, o `null` si la señal
 * descargada cubre todo el estudio.
 *
 * El eje del visor cubre el estudio entero, pero la vista general puede venir
 * más corta: el estudio sigue grabando, o el procesamiento quedó atrás. Ese
 * tramo no es un hueco del equipo y no se puede dibujar vacío ni con otra
 * señal: se marca como "Sin datos procesados".
 */
export function unprocessedTailStartMs(signal: ECGSignal): number | null {
  const processedEndMs = signal.metadata?.processedEndMs
  const endMs = signal.startTimestamp + signal.durationMs
  if (processedEndMs == null || processedEndMs >= endMs) return null
  return Math.max(processedEndMs, signal.startTimestamp)
}

/**
 * El instante "más reciente" al que se ancla el seguimiento en vivo.
 *
 * Con un tramo sin procesar es el final del estudio y no el último punto
 * dibujado: anclarse ahí mandaba la vista "lo último" a una señal vieja.
 */
export function latestTimestampMs(signal: ECGSignal): number {
  if (unprocessedTailStartMs(signal) !== null) return signal.startTimestamp + signal.durationMs
  return signal.timestampsMs[signal.timestampsMs.length - 1] ?? signal.startTimestamp
}
