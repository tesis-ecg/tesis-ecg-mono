import type { StudyPatientReport } from '@/features/studies/types'

import type { ECGSignal } from './types'

export interface EcgDetailWindowRequest {
  id: string
  startEpochMs: number
  endEpochMs: number
}

const OVERVIEWS_PER_PAGE = 4

export function detailWindowRequests(
  signal: ECGSignal,
  reports: StudyPatientReport[],
): EcgDetailWindowRequest[] {
  const raw = [
    ...signal.annotations.map((item) => ({
      id: `annotation:${item.id}`,
      at: (item.startMs + item.endMs) / 2,
    })),
    ...reports
      .filter((report) => report.visibleInChart && report.offsetMs !== null)
      .map((report) => ({
        id: `report:${report.id}`,
        // El backend puede anclar una respuesta a la banda del hallazgo que la
        // originó. `occurredAt` es la hora en que el paciente contestó la
        // alerta (posiblemente mucho después); `offsetMs` es la posición que
        // realmente se ve en el ECG y, por lo tanto, donde corresponde pedir
        // la tira detallada.
        at: signal.startTimestamp + report.offsetMs!,
      })),
  ].sort((a, b) => a.at - b.at)

  const windows: EcgDetailWindowRequest[] = []
  for (const item of raw) {
    const startEpochMs = Math.max(signal.startTimestamp, Math.round(item.at - 5_000))
    const endEpochMs = Math.min(
      signal.startTimestamp + signal.durationMs,
      Math.round(item.at + 5_000),
    )
    const previous = windows.at(-1)
    if (previous && startEpochMs <= previous.endEpochMs) {
      previous.endEpochMs = Math.max(previous.endEpochMs, endEpochMs)
      previous.id = `${previous.id},${item.id}`
    } else if (endEpochMs > startEpochMs) {
      windows.push({ id: item.id, startEpochMs, endEpochMs })
    }
  }
  return windows
}

export function estimateOverviewPages(signal: ECGSignal, sectionMinutes: number): number {
  const sections = Math.max(1, Math.ceil(signal.durationMs / (sectionMinutes * 60_000)))
  return Math.ceil(sections / OVERVIEWS_PER_PAGE)
}
