import { useMemo } from 'react'

import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog'

import { baselineMv, type Amplitude, type PaperSpeed } from '../paperScale'
import { buildStrips, STRIP_SECONDS } from '../printableReport'
import type { ECGSignal, ECGViewportChange } from '../types'
import { formatWallClock } from '../utils/formatEcgTimestamp'

interface ECGPrintableReportProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  signal: ECGSignal
  /** Tramo que el médico está mirando. Es lo que se imprime. */
  viewport: ECGViewportChange | null
  paperSpeed: PaperSpeed
  amplitude: Amplitude
  patientName?: string
  studyStartedAt?: string
}

function indexForEpoch(signal: ECGSignal, epochMs: number, fallback: number): number {
  // Búsqueda binaria sobre `timestampsMs`, que `ecgApi` garantiza monótono.
  const stamps = signal.timestampsMs
  if (stamps.length === 0) return fallback
  let low = 0
  let high = stamps.length - 1
  if (epochMs <= stamps[low]) return 0
  if (epochMs >= stamps[high]) return high
  while (low < high) {
    const mid = (low + high) >> 1
    if (stamps[mid] < epochMs) low = mid + 1
    else high = mid
  }
  return low
}

/**
 * Informe imprimible: tiras de 10 s a escala, con milímetros reales.
 *
 * Es el único lugar del sistema donde la escala de un ECG se puede **garantizar**
 * y no solo aproximar. En pantalla la proporción es exacta pero el milímetro es
 * nominal (px CSS de 1/96", que valen lo que el monitor y el escalado decidan);
 * acá el SVG va dimensionado en `mm` y el motor de impresión los resuelve contra
 * la resolución real del papel o del PDF.
 *
 * Se imprime el tramo que el médico está mirando, no el estudio entero: un
 * registro de 15 días son ~130.000 tiras de 10 s. Quien pide el informe está
 * mirando algo concreto.
 */
export function ECGPrintableReport({
  open,
  onOpenChange,
  signal,
  viewport,
  paperSpeed,
  amplitude,
  patientName,
  studyStartedAt,
}: ECGPrintableReportProps) {
  const strips = useMemo(() => {
    if (!open) return []
    const from = viewport ? indexForEpoch(signal, viewport.startMs, 0) : 0
    const to = viewport
      ? indexForEpoch(signal, viewport.endMs, signal.samples.length)
      : Math.min(signal.samples.length, STRIP_SECONDS * (signal.sampleRate || 500))
    return buildStrips(
      signal,
      from,
      Math.max(to, from + 1),
      { paperSpeed, amplitude },
      (start, end) => baselineMv(signal.samples, start, end),
    )
  }, [open, signal, viewport, paperSpeed, amplitude])

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="flex h-[90vh] max-w-none flex-col gap-3 p-4 sm:max-w-none md:w-[95vw]">
        <DialogHeader className="print:hidden">
          <DialogTitle>Informe imprimible</DialogTitle>
        </DialogHeader>

        <div className="flex items-center gap-2 print:hidden">
          <p className="text-fg-muted text-sm">
            {strips.length} tira{strips.length === 1 ? '' : 's'} de {STRIP_SECONDS} s a {paperSpeed}{' '}
            mm/s · {amplitude} mm/mV. En papel o PDF los milímetros salen exactos; en pantalla, no.
          </p>
          <Button size="sm" className="ml-auto" onClick={() => window.print()}>
            Imprimir
          </Button>
        </div>

        <div className="ecg-print-sheet min-h-0 flex-1 overflow-auto bg-white p-4">
          <header className="ecg-print-header mb-4">
            <h2 className="text-lg font-semibold">{patientName ?? 'Estudio Holter'}</h2>
            <p className="text-fg-muted text-sm">
              {studyStartedAt ? `Inicio del estudio: ${studyStartedAt}. ` : ''}
              Calibración: {paperSpeed} mm/s · {amplitude} mm/mV.
            </p>
          </header>

          {strips.map((strip) => (
            <figure key={strip.startIndex} className="ecg-print-strip mb-3">
              <figcaption className="text-fg-muted mb-1 text-xs tabular-nums">
                {formatWallClock(strip.startEpochMs)}
              </figcaption>
              {/* El SVG se arma como string en `printableReport` porque lleva
                  patrones con ids únicos por tira; inyectarlo evita reconstruir
                  toda esa estructura como JSX sin ganar nada. El contenido es
                  nuestro, no del servidor. */}
              <div dangerouslySetInnerHTML={{ __html: strip.svg }} />
            </figure>
          ))}
        </div>
      </DialogContent>
    </Dialog>
  )
}
