import type { ECGSignal } from '../types'

/**
 * El instante "más reciente" al que se ancla el seguimiento en vivo: el último
 * punto dibujado. El eje termina ahí, porque el tramo que el backend todavía no
 * procesó no se muestra (ver `getStudyEcg`).
 */
export function latestTimestampMs(signal: ECGSignal): number {
  return signal.timestampsMs[signal.timestampsMs.length - 1] ?? signal.startTimestamp
}
