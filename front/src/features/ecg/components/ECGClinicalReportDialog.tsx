import { useEffect, useMemo, useRef, useState } from 'react'
import { Download, FileText, Printer, RotateCcw, X } from 'lucide-react'

import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import type { Patient } from '@/features/patients/types'
import { usePatient } from '@/features/patients/hooks/usePatient'
import type { Study, StudyPatientReport } from '@/features/studies/types'

import { getStudyEcgReportWindows, type EcgReportWindow } from '../api/ecgApi'
import { detailWindowRequests, estimateOverviewPages } from '../clinicalReportPlanning'
import type { ClinicalReportInput } from '../clinicalReportTypes'
import type { Amplitude, PaperSpeed } from '../paperScale'
import type { ECGSignal } from '../types'

interface ECGClinicalReportDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  study: Study
  patientId: string
  signal: ECGSignal
  reports: StudyPatientReport[]
  reportsState: 'loading' | 'error' | 'ready'
  reportsError: string | null
  onRetryReports: () => void
  paperSpeed: PaperSpeed
  amplitude: Amplitude
}

const MAX_WARNING_PAGES = 100

export function ECGClinicalReportDialog({
  open,
  onOpenChange,
  study,
  patientId,
  signal,
  reports,
  reportsState,
  reportsError,
  onRetryReports,
  paperSpeed,
  amplitude,
}: ECGClinicalReportDialogProps) {
  const patientQ = usePatient(patientId)
  const patient: Patient | undefined = patientQ.data
  const [sectionMinutes, setSectionMinutes] = useState('10')
  const [isGenerating, setIsGenerating] = useState(false)
  const [progress, setProgress] = useState<{ completed: number; total: number } | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [pdfUrl, setPdfUrl] = useState<string | null>(null)
  const abortRef = useRef<AbortController | null>(null)
  const parsedMinutes = Number(sectionMinutes)
  const validMinutes = Number.isInteger(parsedMinutes) && parsedMinutes >= 1 && parsedMinutes <= 120
  const overviewPages = validMinutes ? estimateOverviewPages(signal, parsedMinutes) : 0
  const detailCount = useMemo(() => detailWindowRequests(signal, reports).length, [reports, signal])

  useEffect(() => {
    return () => {
      if (pdfUrl) URL.revokeObjectURL(pdfUrl)
    }
  }, [pdfUrl])

  const close = (next: boolean) => {
    if (!next) abortRef.current?.abort()
    onOpenChange(next)
  }

  const generate = async () => {
    if (!patient || !validMinutes || reportsState !== 'ready') return
    abortRef.current?.abort()
    const controller = new AbortController()
    abortRef.current = controller
    setIsGenerating(true)
    setProgress(null)
    setError(null)
    if (pdfUrl) {
      URL.revokeObjectURL(pdfUrl)
      setPdfUrl(null)
    }
    try {
      const requests = splitWindowRequests(detailWindowRequests(signal, reports))
      const detailWindows: EcgReportWindow[] = []
      const batchTotal = Math.ceil(requests.length / 25)
      setProgress({ completed: 0, total: batchTotal + 1 })
      for (let index = 0; index < requests.length; index += 25) {
        const batch = await getStudyEcgReportWindows(
          study.id,
          requests.slice(index, index + 25),
          controller.signal,
        )
        detailWindows.push(...batch)
        setProgress({ completed: index / 25 + 1, total: batchTotal + 1 })
      }
      const input: ClinicalReportInput = {
        study,
        patient,
        signal,
        reports,
        detailWindows,
        sectionMinutes: parsedMinutes,
        paperSpeed,
        amplitude,
        generatedAt: new Date().toISOString(),
      }
      const pdf = await generateInWorker(input, controller.signal)
      if (controller.signal.aborted) return
      setPdfUrl(URL.createObjectURL(new Blob([pdf], { type: 'application/pdf' })))
      setProgress({ completed: batchTotal + 1, total: batchTotal + 1 })
    } catch (cause) {
      if (!controller.signal.aborted) {
        setError(cause instanceof Error ? cause.message : 'No se pudo generar el informe.')
      }
    } finally {
      setIsGenerating(false)
      if (controller.signal.aborted) setProgress(null)
    }
  }

  const download = () => {
    if (!pdfUrl || !patient) return
    const anchor = document.createElement('a')
    anchor.href = pdfUrl
    anchor.download = `informe-ecg-${safeFilename(patient.fullName)}-${study.startedAt.slice(0, 10)}.pdf`
    anchor.click()
  }

  const print = () => {
    if (!pdfUrl) return
    const frame = document.createElement('iframe')
    frame.style.display = 'none'
    frame.src = pdfUrl
    frame.onload = () => {
      frame.contentWindow?.focus()
      frame.contentWindow?.print()
      window.setTimeout(() => frame.remove(), 60_000)
    }
    document.body.append(frame)
  }

  return (
    <Dialog open={open} onOpenChange={close}>
      <DialogContent className="flex max-h-[90vh] max-w-3xl flex-col gap-4 overflow-y-auto">
        <DialogHeader>
          <DialogTitle>Informe clínico ECG</DialogTitle>
          <DialogDescription>
            PDF A4 vertical con snapshot del estudio, señal completa, hallazgos y reportes.
          </DialogDescription>
        </DialogHeader>

        {!patient ? (
          <p className="rounded-md border border-destructive/30 bg-destructive/10 p-3 text-sm text-destructive">
            No se pudieron cargar los datos del paciente. Reintentá antes de generar el informe.
          </p>
        ) : reportsState === 'error' ? (
          <div className="rounded-md border border-destructive/30 bg-destructive/10 p-3 text-sm text-destructive">
            <p>No se pudieron cargar los registros del paciente para el informe.</p>
            {reportsError && <p className="mt-1 text-destructive/80">{reportsError}</p>}
            <Button className="mt-3" variant="outline" size="sm" onClick={onRetryReports}>
              Reintentar
            </Button>
          </div>
        ) : reportsState === 'loading' ? (
          <p className="rounded-md border bg-muted p-3 text-sm text-muted-foreground">
            Cargando registros del paciente para incluirlos en el informe…
          </p>
        ) : (
          <div className="grid gap-2 sm:grid-cols-[minmax(0,12rem)_1fr] sm:items-center">
            <Label htmlFor="ecg-report-minutes">Minutos por bloque overview</Label>
            <Input
              id="ecg-report-minutes"
              type="number"
              min={1}
              max={120}
              value={sectionMinutes}
              onChange={(event) => setSectionMinutes(event.target.value)}
              aria-describedby="ecg-report-minutes-help"
            />
            <p
              id="ecg-report-minutes-help"
              className="text-sm text-muted-foreground sm:col-start-2"
            >
              {validMinutes
                ? `${overviewPages} página${overviewPages === 1 ? '' : 's'} de overview y hasta ${detailCount} página${detailCount === 1 ? '' : 's'} de tiras detalladas.`
                : 'Ingresá un número entero entre 1 y 120.'}
            </p>
          </div>
        )}

        {validMinutes && overviewPages > MAX_WARNING_PAGES && (
          <p className="rounded-md border border-amber-300 bg-amber-50 p-3 text-sm text-amber-900">
            Este informe tendrá más de {MAX_WARNING_PAGES} páginas. Se puede generar, pero puede
            tardar y ser difícil de imprimir.
          </p>
        )}
        {error && (
          <p className="rounded-md border border-destructive/30 bg-destructive/10 p-3 text-sm text-destructive">
            {error}
          </p>
        )}
        {isGenerating && progress && (
          <p className="text-sm text-muted-foreground" aria-live="polite">
            Generando informe: {progress.completed} de {progress.total} pasos completos.
          </p>
        )}

        <div className="flex flex-wrap justify-end gap-2">
          {isGenerating && (
            <Button variant="outline" onClick={() => abortRef.current?.abort()}>
              <X className="size-4" />
              Cancelar
            </Button>
          )}
          <Button
            onClick={() => void generate()}
            disabled={!patient || !validMinutes || reportsState !== 'ready' || isGenerating}
          >
            {pdfUrl ? <RotateCcw className="size-4" /> : <FileText className="size-4" />}
            {isGenerating ? 'Generando…' : pdfUrl ? 'Regenerar' : 'Generar PDF'}
          </Button>
          <Button variant="secondary" onClick={download} disabled={!pdfUrl}>
            <Download className="size-4" />
            Descargar PDF
          </Button>
          <Button variant="secondary" onClick={print} disabled={!pdfUrl}>
            <Printer className="size-4" />
            Imprimir
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  )
}

function splitWindowRequests(requests: ReturnType<typeof detailWindowRequests>) {
  return requests.flatMap((request) => {
    const pieces: (typeof request)[] = []
    for (let start = request.startEpochMs; start < request.endEpochMs; start += 10_000) {
      pieces.push({
        ...request,
        endEpochMs: Math.min(start + 10_000, request.endEpochMs),
        startEpochMs: start,
      })
    }
    return pieces
  })
}

function generateInWorker(input: ClinicalReportInput, signal: AbortSignal): Promise<ArrayBuffer> {
  return new Promise((resolve, reject) => {
    const worker = new Worker(new URL('../clinicalReport.worker.ts', import.meta.url), {
      type: 'module',
    })
    const cancel = () => {
      worker.terminate()
      reject(new DOMException('Generación cancelada.', 'AbortError'))
    }
    if (signal.aborted) return cancel()
    signal.addEventListener('abort', cancel, { once: true })
    worker.onmessage = (
      event: MessageEvent<{ ok: boolean; pdf?: ArrayBuffer; message?: string }>,
    ) => {
      signal.removeEventListener('abort', cancel)
      worker.terminate()
      if (event.data.ok && event.data.pdf) resolve(event.data.pdf)
      else reject(new Error(event.data.message ?? 'No se pudo generar el informe.'))
    }
    worker.onerror = () => {
      signal.removeEventListener('abort', cancel)
      worker.terminate()
      reject(new Error('No se pudo iniciar el generador del informe.'))
    }
    worker.postMessage(input)
  })
}

function safeFilename(value: string): string {
  return (
    value
      .normalize('NFD')
      .replace(/[\u0300-\u036f]/g, '')
      .replace(/[^a-zA-Z0-9]+/g, '-')
      .replace(/^-|-$/g, '')
      .toLowerCase() || 'paciente'
  )
}
