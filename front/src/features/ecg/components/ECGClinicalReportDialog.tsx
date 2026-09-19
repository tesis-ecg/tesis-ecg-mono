import { useEffect, useRef, useState } from 'react'
import {
  CircleAlert,
  Download,
  FileCheck2,
  FileText,
  Printer,
  RotateCcw,
  TriangleAlert,
  X,
} from 'lucide-react'

import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Skeleton } from '@/components/ui/skeleton'
import { useAuth } from '@/features/auth/AuthContext'
import {
  useFinalizeStudyClinicalReport,
  useStudyClinicalReportPreview,
} from '@/features/studies/hooks/useStudyClinicalReport'
import type { Study, StudyClinicalReportPreview } from '@/features/studies/types'
import { unwrapError } from '@/lib/api'
import { cn } from '@/lib/utils'

import {
  getStudyEcgReportWindows,
  type EcgReportWindow,
  type EcgReportWindowRequest,
} from '../api/ecgApi'
import type { ClinicalReportInput } from '../clinicalReportTypes'

interface ECGClinicalReportDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  study: Study
}

export function ECGClinicalReportDialog({
  open,
  onOpenChange,
  study,
}: ECGClinicalReportDialogProps) {
  const { user } = useAuth()
  const previewQ = useStudyClinicalReportPreview(study.id, open)
  const finalizeReport = useFinalizeStudyClinicalReport(study.id)
  const [isGenerating, setIsGenerating] = useState(false)
  const [progress, setProgress] = useState<{ completed: number; total: number } | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [pdfUrl, setPdfUrl] = useState<string | null>(null)
  const [pdfVersion, setPdfVersion] = useState<number | null>(null)
  const [documentStatus, setDocumentStatus] = useState<'draft' | 'final' | null>(null)
  const abortRef = useRef<AbortController | null>(null)

  useEffect(() => {
    return () => {
      if (pdfUrl) URL.revokeObjectURL(pdfUrl)
    }
  }, [pdfUrl])

  const close = (next: boolean) => {
    if (!next) abortRef.current?.abort()
    onOpenChange(next)
  }

  const replacePdf = (pdf: ArrayBuffer, status: 'draft' | 'final', version: number) => {
    const nextUrl = URL.createObjectURL(new Blob([pdf], { type: 'application/pdf' }))
    setPdfUrl(nextUrl)
    setPdfVersion(version)
    setDocumentStatus(status)
  }

  const build = async (
    status: 'draft' | 'final',
  ): Promise<{ pdf: ArrayBuffer; preview: StudyClinicalReportPreview } | null> => {
    abortRef.current?.abort()
    const controller = new AbortController()
    abortRef.current = controller
    setIsGenerating(true)
    setProgress(null)
    setError(null)
    setPdfUrl(null)
    setPdfVersion(null)
    try {
      const refreshed = await previewQ.refetch()
      if (!refreshed.data) throw refreshed.error ?? new Error('No se pudo preparar el informe.')
      const preview = refreshed.data
      if (status === 'final' && !preview.canFinalize) {
        throw new Error(preview.blockingReasons.join(' '))
      }
      const requests = splitWindowRequests(preview.windows)
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
        setProgress({ completed: Math.floor(index / 25) + 1, total: batchTotal + 1 })
      }
      if (status === 'final' && detailWindows.some((window) => window.source !== 'raw')) {
        throw new Error(
          'No se puede finalizar: al menos una tira sólo está disponible como envolvente y no como señal cruda.',
        )
      }
      const input: ClinicalReportInput = {
        snapshot: preview.snapshot,
        windowPlans: preview.windows,
        detailWindows,
        documentStatus: status,
        generatedAt: new Date().toISOString(),
        generatedBy: user ? { fullName: user.fullName, role: user.role } : null,
      }
      const pdf = await generateInWorker(input, controller.signal)
      if (controller.signal.aborted) return null
      replacePdf(pdf, status, preview.nextVersion)
      setProgress({ completed: batchTotal + 1, total: batchTotal + 1 })
      return { pdf, preview }
    } catch (cause) {
      if (!controller.signal.aborted) setError(unwrapError(cause))
      return null
    } finally {
      setIsGenerating(false)
      if (controller.signal.aborted) setProgress(null)
    }
  }

  const finalize = async () => {
    const result = await build('final')
    if (!result) return
    try {
      await finalizeReport.mutateAsync({
        pdf: result.pdf,
        draftRevision: result.preview.draft.revision,
        snapshotHash: result.preview.snapshotHash,
      })
    } catch (cause) {
      setPdfUrl(null)
      setPdfVersion(null)
      setDocumentStatus(null)
      setError(unwrapError(cause))
    }
  }

  const download = () => {
    if (!pdfUrl) return
    const anchor = document.createElement('a')
    anchor.href = pdfUrl
    anchor.download = `informe-holter-${safeFilename(study.patientName)}-v${pdfVersion ?? 1}.pdf`
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

  const busy = isGenerating || finalizeReport.isPending
  return (
    <Dialog open={open} onOpenChange={close}>
      <DialogContent
        className={cn(
          'flex max-h-[90vh] flex-col gap-4',
          busy || pdfUrl ? 'h-[90vh] max-w-6xl overflow-hidden' : 'max-w-3xl overflow-y-auto',
        )}
      >
        <DialogHeader>
          <DialogTitle>Informe clínico Holter</DialogTitle>
          <DialogDescription>
            Genera un PDF con el resumen clínico y únicamente las tiras de los hallazgos
            seleccionados.
          </DialogDescription>
        </DialogHeader>

        {previewQ.isLoading ? (
          <p className="rounded-md border bg-muted p-3 text-sm text-muted-foreground">
            Preparando los datos clínicos del informe…
          </p>
        ) : previewQ.isError ? (
          <div className="rounded-md border border-destructive/30 bg-destructive/10 p-3 text-sm text-destructive">
            <p>{unwrapError(previewQ.error)}</p>
            <Button
              className="mt-3"
              variant="outline"
              size="sm"
              onClick={() => void previewQ.refetch()}
            >
              Reintentar
            </Button>
          </div>
        ) : previewQ.data ? (
          <div className="rounded-md border bg-muted/50 p-3 text-sm text-muted-foreground">
            <p>
              Versión prevista: {previewQ.data.nextVersion} · Tiras: {previewQ.data.windows.length}
            </p>
            {previewQ.data.windows.length === 0 && (
              <p className="mt-1">No hay hallazgos elegibles: el PDF no agregará páginas ECG.</p>
            )}
            <ReportIssues issues={previewQ.data.issues} />
          </div>
        ) : null}

        {error && (
          <p className="rounded-md border border-destructive/30 bg-destructive/10 p-3 text-sm text-destructive">
            {error}
          </p>
        )}
        {busy && progress && (
          <p className="text-sm text-muted-foreground" aria-live="polite">
            Generando informe: {progress.completed} de {progress.total} pasos completos.
          </p>
        )}
        {documentStatus === 'final' && !finalizeReport.isPending && !error && (
          <p className="rounded-md border border-emerald-300 bg-emerald-50 p-3 text-sm text-emerald-900">
            La versión final quedó almacenada y ya está disponible en el historial.
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
            onClick={() => void build('draft')}
            disabled={!previewQ.data?.canGenerateDraft || busy}
          >
            {pdfUrl ? <RotateCcw className="size-4" /> : <FileText className="size-4" />}
            {isGenerating ? 'Generando…' : pdfUrl ? 'Regenerar borrador' : 'Generar borrador'}
          </Button>
          <Button
            variant="secondary"
            onClick={() => void finalize()}
            disabled={!previewQ.data?.canFinalize || busy}
          >
            <FileCheck2 className="size-4" />
            {finalizeReport.isPending ? 'Finalizando…' : 'Generar informe final'}
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

        {(busy || pdfUrl) && (
          <section
            className="min-h-72 flex-1 overflow-hidden rounded-lg border border-border bg-muted/40"
            aria-label="Vista previa del informe clínico ECG"
            aria-busy={busy}
          >
            {busy ? (
              <div
                className="flex h-full min-h-72 items-start justify-center overflow-hidden p-4 sm:p-6"
                role="status"
                aria-label="Preparando vista previa del informe"
                data-testid="clinical-report-preview-skeleton"
              >
                <div className="h-[56rem] w-full max-w-[40rem] rounded-md border bg-background p-8 shadow-sm">
                  <Skeleton className="mb-8 h-7 w-2/3" />
                  <Skeleton className="mb-3 h-4 w-full" />
                  <Skeleton className="mb-3 h-4 w-5/6" />
                  <Skeleton className="mb-8 h-4 w-3/4" />
                  <Skeleton className="mb-8 h-44 w-full" />
                  <Skeleton className="mb-3 h-4 w-full" />
                </div>
                <span className="sr-only">Generando la vista previa del PDF…</span>
              </div>
            ) : pdfUrl ? (
              <object
                data={pdfUrl}
                type="application/pdf"
                className="h-full min-h-72 w-full bg-background"
                aria-label="Vista previa del informe clínico ECG en PDF"
                data-testid="clinical-report-pdf-preview"
              >
                <div className="flex h-full min-h-72 flex-col items-center justify-center gap-3 p-6 text-center">
                  <p className="text-sm text-muted-foreground">
                    Este navegador no puede mostrar el PDF dentro de la aplicación.
                  </p>
                  <Button variant="secondary" onClick={download}>
                    <Download className="size-4" />
                    Descargar PDF
                  </Button>
                </div>
              </object>
            ) : null}
          </section>
        )}
      </DialogContent>
    </Dialog>
  )
}

function ReportIssues({ issues }: { issues: StudyClinicalReportPreview['issues'] }) {
  const blocking = issues.filter((issue) => issue.severity === 'blocking')
  const warnings = issues.filter((issue) => issue.severity === 'warning')
  if (blocking.length === 0 && warnings.length === 0) return null

  return (
    <div className="mt-3 grid gap-2">
      {blocking.length > 0 && (
        <div className="rounded-md border border-destructive/30 bg-destructive/10 p-3 text-destructive">
          <p className="flex items-center gap-2 font-medium">
            <CircleAlert className="size-4" aria-hidden />
            Faltan datos para generar la versión final
          </p>
          <ul className="mt-1 list-disc pl-6 text-sm">
            {blocking.map((issue) => (
              <li key={issue.code}>{issue.message}</li>
            ))}
          </ul>
        </div>
      )}
      {warnings.length > 0 && (
        <div className="rounded-md border border-amber-300 bg-amber-50 p-3 text-amber-900">
          <p className="flex items-center gap-2 font-medium">
            <TriangleAlert className="size-4" aria-hidden />
            Advertencias
          </p>
          <ul className="mt-1 list-disc pl-6 text-sm">
            {warnings.map((issue) => (
              <li key={issue.code}>{issue.message}</li>
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}

function splitWindowRequests(
  windows: StudyClinicalReportPreview['windows'],
): EcgReportWindowRequest[] {
  return windows.flatMap((window) => {
    const pieces: EcgReportWindowRequest[] = []
    for (let start = window.startEpochMs; start < window.endEpochMs; start += 10_000) {
      pieces.push({
        id: window.id,
        startEpochMs: start,
        endEpochMs: Math.min(start + 10_000, window.endEpochMs),
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
