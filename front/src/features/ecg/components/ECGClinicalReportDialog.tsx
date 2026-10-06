import { useEffect, useEffectEvent, useRef, useState } from 'react'
import {
  CircleAlert,
  CircleCheck,
  Download,
  FileCheck2,
  FileText,
  Lock,
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

import {
  getStudyEcgReportWindows,
  type EcgReportWindow,
  type EcgReportWindowRequest,
} from '../api/ecgApi'
import type { ClinicalReportInput } from '../clinicalReportTypes'

export type ClinicalReportMode = 'draft' | 'final'

interface ECGClinicalReportDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  study: Study
  /** Qué abrió el diálogo: el borrador se genera solo, el final pide confirmación. */
  mode: ClinicalReportMode
}

export function ECGClinicalReportDialog({
  open,
  onOpenChange,
  study,
  mode,
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

  const autoStartedRef = useRef(false)

  const close = (next: boolean) => {
    if (!next) {
      abortRef.current?.abort()
      abortRef.current = null
      setIsGenerating(false)
      // Al volver a abrir (quizás en el otro modo) no tiene que quedar el PDF anterior.
      autoStartedRef.current = false
      setPdfUrl(null)
      setPdfVersion(null)
      setDocumentStatus(null)
      setError(null)
      setProgress(null)
    }
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
      if (controller.signal.aborted) return null
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
        if (controller.signal.aborted) return null
        detailWindows.push(...batch)
        setProgress({ completed: Math.floor(index / 25) + 1, total: batchTotal + 1 })
      }
      if (status === 'final' && detailWindows.some((window) => window.source === 'envelope')) {
        throw new Error(
          'No se puede finalizar: al menos una tira sólo está disponible como envolvente y no como señal cruda.',
        )
      }
      if (status === 'final' && detailWindows.some((window) => window.samplesMv.length === 0)) {
        throw new Error('No se puede finalizar: una tira todavía no tiene datos procesados.')
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
      // Una generación cerrada no debe limpiar el estado de la que la reemplazó.
      if (abortRef.current === controller) {
        abortRef.current = null
        setIsGenerating(false)
        if (controller.signal.aborted) setProgress(null)
      }
    }
  }

  // Generar un borrador no tiene consecuencias: arranca solo al abrir. El final
  // guarda una versión inmutable y siempre espera el click.
  const startDraft = useEffectEvent(() => void build('draft'))
  const canAutoStart = open && mode === 'draft' && Boolean(previewQ.data?.canGenerateDraft)
  useEffect(() => {
    if (!canAutoStart || autoStartedRef.current) return
    autoStartedRef.current = true
    startDraft()
  }, [canAutoStart])

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
  const report = previewQ.data
  const isFinal = mode === 'final'
  const finalized = isFinal && documentStatus === 'final' && !finalizeReport.isPending && !error
  return (
    <Dialog open={open} onOpenChange={close}>
      <DialogContent className="flex h-[90vh] max-h-[90vh] flex-col gap-0 overflow-hidden p-0 sm:max-w-6xl lg:grid lg:grid-cols-[minmax(18rem,22rem)_1fr]">
        <div className="flex min-h-0 flex-col gap-5 overflow-y-auto border-b border-border p-6 lg:border-r lg:border-b-0">
          <DialogHeader className="pr-6">
            <DialogTitle>{isFinal ? 'Informe final' : 'Borrador del informe'}</DialogTitle>
            <DialogDescription>
              {isFinal
                ? 'Genera la versión definitiva del informe Holter y la guarda en el historial del estudio.'
                : 'Vista previa del informe Holter con el resumen clínico y las tiras de los hallazgos seleccionados.'}
            </DialogDescription>
          </DialogHeader>

          {previewQ.isLoading ? (
            <div className="flex flex-col gap-2" aria-label="Preparando los datos del informe">
              <Skeleton className="h-4 w-2/3" />
              <Skeleton className="h-4 w-1/2" />
            </div>
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
          ) : report ? (
            <div className="rounded-md border border-border bg-muted/40 p-4 text-sm">
              <dl className="grid grid-cols-2 gap-3">
                <div>
                  <dt className="text-body3 text-gray-500">Versión prevista</dt>
                  <dd className="mt-0.5 font-medium text-gray-900">{report.nextVersion}</dd>
                </div>
                <div>
                  <dt className="text-body3 text-gray-500">Tiras de ECG</dt>
                  <dd className="mt-0.5 font-medium text-gray-900">{report.windows.length}</dd>
                </div>
              </dl>
              {report.windows.length === 0 && (
                <p className="mt-3 text-muted-foreground">
                  No hay hallazgos elegibles: el PDF no agregará páginas ECG.
                </p>
              )}
              <ReportIssues issues={report.issues} canGenerateDraft={report.canGenerateDraft} />
            </div>
          ) : null}

          {isFinal && report && !finalized && (
            <p className="flex gap-2.5 rounded-md border border-warning-300 bg-warning-100 p-3 text-sm text-warning-700">
              <Lock className="mt-0.5 size-4 shrink-0" aria-hidden />
              <span>
                Se guardará como versión {report.nextVersion} y no podrá modificarse después.
              </span>
            </p>
          )}

          {error && (
            <p className="rounded-md border border-destructive/30 bg-destructive/10 p-3 text-sm text-destructive">
              {error}
            </p>
          )}
          {finalized && (
            <p className="flex gap-2.5 rounded-md border border-success-200 bg-success-100 p-3 text-sm text-success-700">
              <CircleCheck className="mt-0.5 size-4 shrink-0" aria-hidden />
              <span>La versión final quedó guardada y ya está disponible en el historial.</span>
            </p>
          )}

          <div className="mt-auto flex flex-col gap-3 pt-2">
            {busy && progress && (
              <div className="flex flex-col gap-1.5" aria-live="polite">
                <div className="flex justify-between text-body3 text-gray-500">
                  <span>{finalizeReport.isPending ? 'Guardando versión…' : 'Generando PDF…'}</span>
                  <span>
                    Paso {Math.min(progress.completed + 1, progress.total)} de {progress.total}
                  </span>
                </div>
                <div className="h-1.5 overflow-hidden rounded-full bg-gray-100">
                  <div
                    className="h-full rounded-full bg-primary transition-[width] duration-300"
                    style={{ width: `${(progress.completed / progress.total) * 100}%` }}
                  />
                </div>
              </div>
            )}
            <div className="flex gap-2">
              {isGenerating && (
                <Button variant="outline" onClick={() => abortRef.current?.abort()}>
                  <X className="size-4" />
                  Cancelar
                </Button>
              )}
              {finalized ? (
                <Button className="flex-1" variant="outline" onClick={() => close(false)}>
                  Cerrar
                </Button>
              ) : isFinal ? (
                <Button
                  className="flex-1"
                  onClick={() => void finalize()}
                  disabled={!report?.canFinalize || busy}
                >
                  <FileCheck2 className="size-4" />
                  {finalizeReport.isPending
                    ? 'Guardando…'
                    : isGenerating
                      ? 'Generando…'
                      : 'Generar informe final'}
                </Button>
              ) : (
                <Button
                  className="flex-1"
                  onClick={() => void build('draft')}
                  disabled={!report?.canGenerateDraft || busy}
                >
                  {pdfUrl ? <RotateCcw className="size-4" /> : <FileText className="size-4" />}
                  {isGenerating ? 'Generando…' : pdfUrl ? 'Regenerar borrador' : 'Generar borrador'}
                </Button>
              )}
            </div>
          </div>
        </div>

        <section
          className="flex min-h-80 flex-1 flex-col gap-3 bg-muted/40 p-4 lg:min-h-0 lg:p-6 lg:pt-12"
          aria-label="Vista previa del informe clínico ECG"
          aria-busy={busy}
        >
          <div className="min-h-0 flex-1 overflow-hidden rounded-lg border border-border bg-background">
            {busy ? (
              <div
                className="flex h-full items-start justify-center overflow-hidden p-4 sm:p-6"
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
                className="h-full w-full bg-background"
                aria-label="Vista previa del informe clínico ECG en PDF"
                data-testid="clinical-report-pdf-preview"
              >
                <div className="flex h-full flex-col items-center justify-center gap-2 p-6 text-center">
                  <p className="text-sm text-muted-foreground">
                    Este navegador no puede mostrar el PDF dentro de la aplicación. Podés
                    descargarlo con el botón de abajo.
                  </p>
                </div>
              </object>
            ) : (
              <div className="flex h-full flex-col items-center justify-center gap-3 p-6 text-center">
                <span className="flex size-12 items-center justify-center rounded-full bg-primary-50 text-primary">
                  <FileText className="size-6" aria-hidden />
                </span>
                <div>
                  <p className="text-body2 font-medium text-gray-900">
                    La vista previa del PDF aparecerá acá
                  </p>
                  <p className="mt-1 text-body3 text-gray-500">
                    {isFinal
                      ? 'Generá el informe final para verlo, descargarlo o imprimirlo.'
                      : 'Generá el borrador para verlo, descargarlo o imprimirlo.'}
                  </p>
                </div>
              </div>
            )}
          </div>

          {pdfUrl && !busy && (
            <div className="flex flex-wrap items-center justify-between gap-2">
              <p className="text-body3 text-gray-500">
                {documentStatus === 'final'
                  ? `Versión final ${pdfVersion ?? ''}`.trim()
                  : 'Borrador'}
              </p>
              <div className="flex gap-2">
                <Button variant="outline" onClick={print}>
                  <Printer className="size-4" />
                  Imprimir
                </Button>
                <Button onClick={download}>
                  <Download className="size-4" />
                  Descargar PDF
                </Button>
              </div>
            </div>
          )}
        </section>
      </DialogContent>
    </Dialog>
  )
}

function ReportIssues({
  issues,
  canGenerateDraft,
}: {
  issues: StudyClinicalReportPreview['issues']
  canGenerateDraft: boolean
}) {
  const blocking = issues.filter((issue) => issue.severity === 'blocking')
  const warnings = issues.filter((issue) => issue.severity === 'warning')
  if (blocking.length === 0 && warnings.length === 0) return null

  // El detalle de cada requisito vive en el checklist de la pestaña "Informe clínico";
  // acá sólo se resume para no recargar el diálogo.
  return (
    <ul className="mt-3 flex flex-col gap-2 border-t border-border pt-3">
      {blocking.length > 0 && (
        <li className="flex gap-2.5">
          <CircleAlert className="mt-0.5 size-4 shrink-0 text-warning-700" aria-hidden />
          <span>
            <span className="font-medium text-gray-900">
              {blocking.length === 1
                ? 'Falta 1 requisito para la versión final'
                : `Faltan ${blocking.length} requisitos para la versión final`}
            </span>
            {canGenerateDraft && ' · podés generar un borrador igualmente.'}
          </span>
        </li>
      )}
      {warnings.map((issue) => (
        <li key={issue.code} className="flex gap-2.5">
          <TriangleAlert className="mt-0.5 size-4 shrink-0 text-warning-700" aria-hidden />
          <span>{issue.message}</span>
        </li>
      ))}
    </ul>
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
    let worker: Worker | null = null
    let settled = false
    let fallingBack = false
    const cleanup = () => {
      signal.removeEventListener('abort', cancel)
      worker?.terminate()
    }
    const finish = (result: ArrayBuffer) => {
      if (settled) return
      settled = true
      cleanup()
      resolve(result)
    }
    const fail = (cause: unknown) => {
      if (settled) return
      settled = true
      cleanup()
      reject(cause)
    }
    const cancel = () => {
      fail(new DOMException('Generación cancelada.', 'AbortError'))
    }
    const startFallback = () => {
      if (settled || fallingBack) return
      fallingBack = true
      worker?.terminate()
      void generateOnMainThread(input, signal).then(finish, fail)
    }
    if (signal.aborted) return cancel()
    signal.addEventListener('abort', cancel, { once: true })
    try {
      worker = new Worker(new URL('../clinicalReport.worker.ts', import.meta.url), {
        type: 'module',
      })
      worker.onmessage = (
        event: MessageEvent<{ ok: boolean; pdf?: ArrayBuffer; message?: string }>,
      ) => {
        if (event.data.ok && event.data.pdf) finish(event.data.pdf)
        else fail(new Error(event.data.message ?? 'No se pudo generar el informe.'))
      }
      worker.onerror = startFallback
      worker.postMessage(input)
    } catch {
      startFallback()
    }
  })
}

async function generateOnMainThread(
  input: ClinicalReportInput,
  signal: AbortSignal,
): Promise<ArrayBuffer> {
  if (signal.aborted) throw new DOMException('Generación cancelada.', 'AbortError')
  const { buildClinicalReport } = await import('../clinicalReport')
  if (signal.aborted) throw new DOMException('Generación cancelada.', 'AbortError')
  return buildClinicalReport(input)
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
