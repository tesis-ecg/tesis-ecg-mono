import { useState } from 'react'
import type { ReactNode } from 'react'
import {
  CircleAlert,
  CircleCheck,
  CircleX,
  Download,
  Eye,
  FileCheck2,
  RefreshCw,
  Save,
  TriangleAlert,
} from 'lucide-react'

import { EmptyState } from '@/components/EmptyState'
import { Spinner } from '@/components/Spinner'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'
import { formatDateTime } from '@/lib/time'
import { downloadStudyClinicalReport } from '@/features/studies/api/studiesApi'
import {
  useStudyClinicalReportDraft,
  useStudyClinicalReportPreview,
  useStudyClinicalReportVersions,
  useUpdateStudyClinicalReportDraft,
} from '@/features/studies/hooks/useStudyClinicalReport'
import type {
  Study,
  StudyClinicalReportDraft,
  StudyClinicalReportIssue,
  StudyClinicalReportPreview,
} from '@/features/studies/types'
import { isApiError, unwrapError } from '@/lib/api'
import { cn } from '@/lib/utils'

type PreviewState = 'loading' | 'error' | 'ready'

// Requisitos conocidos para emitir el informe final. El backend sólo devuelve los que fallan,
// así que este catálogo permite mostrar también los cumplidos.
const REPORT_REQUIREMENTS: { code: string; label: string; fieldId?: string }[] = [
  { code: 'STUDY_NOT_COMPLETED', label: 'Estudio completado' },
  { code: 'TIME_NOT_VERIFIED', label: 'Hora de las muestras verificada' },
  { code: 'MISSING_RAW_SIGNAL', label: 'Señal cruda disponible' },
  { code: 'BEAT_ANALYSIS_PENDING', label: 'Análisis de latidos completo' },
  // Las métricas excluyen el ruido que marca el motor: hasta que cubre toda la
  // señal, el informe final no las puede congelar.
  { code: 'ML_ANALYSIS_PENDING', label: 'Análisis automático completo' },
  { code: 'MISSING_INDICATION', label: 'Indicación del estudio', fieldId: 'report-indication' },
  { code: 'MISSING_CONCLUSION', label: 'Conclusión clínica', fieldId: 'report-conclusion' },
]

interface RequirementItem {
  code: string
  label: string
  fieldId?: string
  issue?: StudyClinicalReportIssue
}

function buildRequirements(preview: StudyClinicalReportPreview) {
  const blocking = preview.issues.filter((issue) => issue.severity === 'blocking')
  const warnings = preview.issues.filter((issue) => issue.severity === 'warning')
  const known = new Set(REPORT_REQUIREMENTS.map((item) => item.code))
  const items: RequirementItem[] = [
    ...REPORT_REQUIREMENTS.map((item) => ({
      ...item,
      issue: blocking.find((issue) => issue.code === item.code),
    })),
    // Requisitos nuevos del backend que el catálogo todavía no conoce.
    ...blocking
      .filter((issue) => !known.has(issue.code))
      .map((issue) => ({ code: issue.code, label: issue.message, issue })),
  ]
  return { items, pending: items.filter((item) => item.issue), warnings }
}

interface StudyClinicalReportTabProps {
  study: Study
  onPreview: () => void
}

export function StudyClinicalReportTab({ study, onPreview }: StudyClinicalReportTabProps) {
  const draftQ = useStudyClinicalReportDraft(study.id)
  const previewQ = useStudyClinicalReportPreview(study.id)
  const versionsQ = useStudyClinicalReportVersions(study.id)
  const previewState: PreviewState = previewQ.isLoading
    ? 'loading'
    : previewQ.isError
      ? 'error'
      : 'ready'

  if (draftQ.isLoading) return <Spinner label="Cargando informe clínico…" />
  if (draftQ.isError || !draftQ.data) {
    return (
      <EmptyState
        title="No pudimos cargar el informe clínico"
        description={unwrapError(draftQ.error)}
        action={
          <Button variant="outline" onClick={() => void draftQ.refetch()}>
            Reintentar
          </Button>
        }
      />
    )
  }

  return (
    <div className="grid gap-4 xl:grid-cols-[minmax(0,2fr)_minmax(19rem,1fr)]">
      <ClinicalReportForm
        key={draftQ.data.revision}
        study={study}
        draft={draftQ.data}
        preview={previewQ.data}
        previewState={previewState}
        onReload={() => void draftQ.refetch()}
        onPreview={onPreview}
      />
      <div className="flex flex-col gap-4">
        <ReportRequirementsCard
          preview={previewQ.data}
          state={previewState}
          error={previewQ.error}
        />
        <Card className="flex flex-col gap-4 p-5">
          <div>
            <h3 className="text-h6 text-gray-900">Resumen del informe</h3>
            <p className="mt-1 text-body3 text-gray-500">
              Estado técnico y hallazgos incluidos en el snapshot actual.
            </p>
          </div>
          {previewQ.isLoading ? (
            <Spinner label="Calculando resumen…" />
          ) : previewQ.isError ? (
            <p className="text-sm text-destructive">{unwrapError(previewQ.error)}</p>
          ) : previewQ.data ? (
            <dl className="grid grid-cols-2 gap-3 text-sm">
              <SummaryTerm
                label="Cobertura"
                value={`${previewQ.data.snapshot.quality.coveragePercent.toFixed(1)} %`}
              />
              <SummaryTerm
                label="Interrupciones"
                value={formatDuration(previewQ.data.snapshot.quality.interruptionMs)}
              />
              <SummaryTerm
                label="Tipos de hallazgo"
                value={String(previewQ.data.snapshot.findings.length)}
              />
              <SummaryTerm label="Tiras previstas" value={String(previewQ.data.windows.length)} />
            </dl>
          ) : null}
        </Card>

        <Card className="flex flex-col gap-4 p-5">
          <div className="flex items-center justify-between gap-3">
            <div>
              <h3 className="text-h6 text-gray-900">Versiones finales</h3>
              <p className="mt-1 text-body3 text-gray-500">Historial inmutable almacenado.</p>
            </div>
            <Badge variant="neutral">{versionsQ.data?.length ?? 0}</Badge>
          </div>
          {versionsQ.isLoading ? (
            <Spinner label="Cargando versiones…" />
          ) : versionsQ.isError ? (
            <p className="text-sm text-destructive">{unwrapError(versionsQ.error)}</p>
          ) : versionsQ.data?.length ? (
            <div className="flex flex-col divide-y divide-border">
              {versionsQ.data.map((version) => (
                <div key={version.id} className="flex items-center justify-between gap-3 py-3">
                  <div className="min-w-0">
                    <p className="text-sm font-medium text-gray-900">Versión {version.version}</p>
                    <p className="truncate text-body3 text-gray-500">
                      {formatDate(version.finalizedAt)} · {version.finalizedByName}
                    </p>
                  </div>
                  <Button
                    variant="outline"
                    size="sm"
                    aria-label={`Descargar versión ${version.version}`}
                    onClick={() => void downloadVersion(study.id, version.id, version.version)}
                  >
                    <Download className="size-4" />
                  </Button>
                </div>
              ))}
            </div>
          ) : (
            <p className="text-sm text-muted-foreground">Todavía no hay versiones finalizadas.</p>
          )}
        </Card>
      </div>
    </div>
  )
}

function ClinicalReportForm({
  study,
  draft,
  preview: reportPreview,
  previewState,
  onReload,
  onPreview,
}: {
  study: Study
  draft: StudyClinicalReportDraft
  preview: StudyClinicalReportPreview | undefined
  previewState: PreviewState
  onReload: () => void
  onPreview: () => void
}) {
  const update = useUpdateStudyClinicalReportDraft(study.id)
  const [values, setValues] = useState({
    indication: draft.indication ?? '',
    medications: draft.medications ?? '',
    referringProfessional: draft.referringProfessional ?? '',
    technician: draft.technician ?? '',
    clinicalObservations: draft.clinicalObservations ?? '',
    conclusion: draft.conclusion ?? '',
  })
  const [saved, setSaved] = useState(false)
  const [dirty, setDirty] = useState(false)
  const set = (field: keyof typeof values, value: string) => {
    setSaved(false)
    setDirty(true)
    setValues((current) => ({ ...current, [field]: value }))
  }
  const save = async (): Promise<boolean> => {
    setSaved(false)
    try {
      await update.mutateAsync({
        revision: draft.revision,
        indication: values.indication.trim() || null,
        medications: values.medications.trim() || null,
        referringProfessional: values.referringProfessional.trim() || null,
        technician: values.technician.trim() || null,
        clinicalObservations: values.clinicalObservations.trim() || null,
        conclusion: values.conclusion.trim() || null,
      })
      setSaved(true)
      setDirty(false)
      return true
    } catch {
      // El estado de error de la mutación muestra el conflicto o fallo debajo del formulario.
      return false
    }
  }
  const preview = async () => {
    if (dirty && !(await save())) return
    onPreview()
  }
  const conflict = isApiError(update.error) && update.error.code === 'CONFLICT'
  const pendingCodes = new Set(
    reportPreview?.issues
      .filter((issue) => issue.severity === 'blocking')
      .map((issue) => issue.code) ?? [],
  )
  const requiredHint = (code: string) =>
    pendingCodes.has(code) ? 'Requerido para finalizar' : undefined

  return (
    <Card className="flex flex-col gap-6 p-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-h5 text-gray-900">Datos clínicos e interpretación</h2>
          <p className="mt-1 text-sm text-gray-500">
            Podés guardar el borrador en cualquier momento mientras el estudio está en curso.
          </p>
          <p className="mt-1 text-body3 text-gray-500">
            Revisión {draft.revision}
            {draft.updatedByName ? ` · última edición por ${draft.updatedByName}` : ''}
            {draft.updatedAt ? ` · ${formatDate(draft.updatedAt)}` : ''}
          </p>
        </div>
        <Badge variant={study.status === 'completed' ? 'success' : 'warning'}>
          {study.status === 'completed' ? 'Estudio completado' : 'Sólo borrador'}
        </Badge>
      </div>

      <ReadinessStatus preview={reportPreview} state={previewState} />

      <div className="grid gap-4 md:grid-cols-2">
        <Field
          label="Indicación del estudio"
          htmlFor="report-indication"
          hint={requiredHint('MISSING_INDICATION')}
          className="md:col-span-2"
        >
          <Textarea
            id="report-indication"
            maxLength={4000}
            value={values.indication}
            onChange={(event) => set('indication', event.target.value)}
          />
        </Field>
        <Field label="Profesional derivante" htmlFor="report-referring">
          <Input
            id="report-referring"
            maxLength={240}
            value={values.referringProfessional}
            onChange={(event) => set('referringProfessional', event.target.value)}
          />
        </Field>
        <Field label="Técnico responsable" htmlFor="report-technician">
          <Input
            id="report-technician"
            maxLength={240}
            value={values.technician}
            onChange={(event) => set('technician', event.target.value)}
          />
        </Field>
        <Field
          label="Medicación durante el estudio"
          htmlFor="report-medications"
          className="md:col-span-2"
        >
          <Textarea
            id="report-medications"
            maxLength={8000}
            value={values.medications}
            onChange={(event) => set('medications', event.target.value)}
          />
        </Field>
        <Field
          label="Observaciones clínicas"
          htmlFor="report-observations"
          className="md:col-span-2"
        >
          <Textarea
            id="report-observations"
            maxLength={8000}
            value={values.clinicalObservations}
            onChange={(event) => set('clinicalObservations', event.target.value)}
          />
        </Field>
        <Field
          label="Conclusión / interpretación final"
          htmlFor="report-conclusion"
          hint={requiredHint('MISSING_CONCLUSION')}
          className="md:col-span-2"
        >
          <Textarea
            id="report-conclusion"
            maxLength={12000}
            className="min-h-32"
            value={values.conclusion}
            onChange={(event) => set('conclusion', event.target.value)}
          />
        </Field>
      </div>

      {update.isError && (
        <div className="rounded-md border border-destructive/30 bg-destructive/10 p-3 text-sm text-destructive">
          <p>
            {conflict
              ? 'El borrador cambió en otra sesión. Recargalo antes de continuar.'
              : unwrapError(update.error)}
          </p>
          {conflict && (
            <Button variant="outline" size="sm" className="mt-2" onClick={onReload}>
              <RefreshCw className="size-4" />
              Recargar
            </Button>
          )}
        </div>
      )}
      {saved && <p className="text-sm text-success-700">Borrador guardado.</p>}

      <div className="flex flex-wrap justify-end gap-2">
        <Button
          variant="outline"
          onClick={() => void preview()}
          disabled={
            update.isPending || previewState !== 'ready' || !reportPreview?.canGenerateDraft
          }
        >
          <Eye className="size-4" />
          Previsualizar borrador
        </Button>
        <Button onClick={() => void save()} disabled={update.isPending || !dirty}>
          <Save className="size-4" />
          {update.isPending ? 'Guardando…' : 'Guardar borrador'}
        </Button>
        <Button
          variant="secondary"
          onClick={() => void preview()}
          disabled={update.isPending || previewState !== 'ready' || !reportPreview?.canFinalize}
        >
          <FileCheck2 className="size-4" />
          Generar informe final
        </Button>
      </div>
    </Card>
  )
}

function ReadinessStatus({
  preview,
  state,
}: {
  preview: StudyClinicalReportPreview | undefined
  state: PreviewState
}) {
  let icon: ReactNode
  let text: ReactNode
  if (state === 'loading') {
    icon = <Spinner size="sm" />
    text = 'Verificando requisitos del informe…'
  } else if (state === 'error' || !preview) {
    icon = <CircleAlert className="size-4 text-destructive" aria-hidden />
    text = 'No se pudo verificar si el informe puede emitirse.'
  } else {
    const { pending, warnings } = buildRequirements(preview)
    const warningText =
      warnings.length > 0
        ? ` · ${warnings.length} ${warnings.length === 1 ? 'advertencia' : 'advertencias'}`
        : ''
    if (pending.length === 0) {
      icon = <CircleCheck className="size-4 text-success-700" aria-hidden />
      text = (
        <>
          <span className="font-medium text-gray-900">Listo para emitir el informe final</span>
          <span className="text-gray-500">{warningText}</span>
        </>
      )
    } else {
      icon = <CircleAlert className="size-4 text-warning-700" aria-hidden />
      text = (
        <>
          <span className="font-medium text-gray-900">
            {pending.length === 1
              ? 'Falta 1 requisito para emitir el informe final'
              : `Faltan ${pending.length} requisitos para emitir el informe final`}
          </span>
          <span className="text-gray-500">{warningText}</span>
        </>
      )
    }
  }

  return (
    <div
      aria-live="polite"
      className="flex items-center gap-2.5 rounded-md border border-border bg-muted/40 px-3 py-2 text-sm text-gray-600"
    >
      <span className="flex shrink-0 items-center">{icon}</span>
      <p className="min-w-0">{text}</p>
    </div>
  )
}

function ReportRequirementsCard({
  preview,
  state,
  error,
}: {
  preview: StudyClinicalReportPreview | undefined
  state: PreviewState
  error: unknown
}) {
  const summary = preview ? buildRequirements(preview) : null
  const done = summary ? summary.items.length - summary.pending.length : 0

  return (
    <Card className="flex flex-col gap-4 p-5">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h3 className="text-h6 text-gray-900">Requisitos para finalizar</h3>
          <p className="mt-1 text-body3 text-gray-500">
            Condiciones para emitir una versión final.
          </p>
        </div>
        {summary && (
          <Badge variant={summary.pending.length === 0 ? 'success' : 'neutral'}>
            {done}/{summary.items.length}
          </Badge>
        )}
      </div>

      {state === 'loading' ? (
        <Spinner label="Verificando requisitos…" />
      ) : state === 'error' || !summary ? (
        <p className="text-sm text-destructive">{unwrapError(error)}</p>
      ) : (
        <>
          <ul className="flex flex-col gap-1">
            {summary.items.map((item) => (
              <RequirementRow key={item.code} item={item} />
            ))}
          </ul>
          {summary.warnings.length > 0 && (
            <div className="flex flex-col gap-2 border-t border-border pt-4">
              <p className="text-body3 font-medium tracking-wide text-gray-500 uppercase">
                Advertencias
              </p>
              <ul className="flex flex-col gap-2">
                {summary.warnings.map((issue) => (
                  <li key={issue.code} className="flex gap-2.5 text-sm text-gray-600">
                    <TriangleAlert
                      className="mt-0.5 size-4 shrink-0 text-warning-700"
                      aria-hidden
                    />
                    <span>{issue.message}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </>
      )}
    </Card>
  )
}

function RequirementRow({ item }: { item: RequirementItem }) {
  const pending = Boolean(item.issue)
  const content = (
    <>
      {pending ? (
        <CircleX className="mt-0.5 size-4 shrink-0 text-destructive" aria-hidden />
      ) : (
        <CircleCheck className="mt-0.5 size-4 shrink-0 text-success-700" aria-hidden />
      )}
      <span className="min-w-0 flex-1">
        <span
          className={cn('block text-sm', pending ? 'font-medium text-gray-900' : 'text-gray-600')}
        >
          {item.label}
          <span className="sr-only">{pending ? ' (pendiente)' : ' (cumplido)'}</span>
        </span>
        {/* En los campos del formulario la acción "Completar" ya explica qué falta. */}
        {pending && !item.fieldId && item.issue && item.issue.message !== item.label && (
          <span className="mt-0.5 block text-body3 text-gray-500">{item.issue.message}</span>
        )}
      </span>
      {pending && item.fieldId && (
        <span className="shrink-0 text-body3 font-medium text-primary">Completar</span>
      )}
    </>
  )

  if (pending && item.fieldId) {
    const fieldId = item.fieldId
    return (
      <li>
        <button
          type="button"
          className="-mx-2 flex w-[calc(100%+1rem)] gap-2.5 rounded-md px-2 py-1.5 text-left transition-colors hover:bg-muted/60 focus-visible:ring-[3px] focus-visible:ring-ring/50 focus-visible:outline-none"
          onClick={() => {
            const field = document.getElementById(fieldId)
            field?.scrollIntoView({ behavior: 'smooth', block: 'center' })
            field?.focus({ preventScroll: true })
          }}
        >
          {content}
        </button>
      </li>
    )
  }

  return <li className="flex gap-2.5 py-1.5">{content}</li>
}

function Field({
  label,
  htmlFor,
  hint,
  className,
  children,
}: {
  label: string
  htmlFor: string
  hint?: string
  className?: string
  children: ReactNode
}) {
  return (
    <div className={className}>
      <div className="flex items-baseline justify-between gap-3">
        <Label htmlFor={htmlFor}>{label}</Label>
        {hint && <span className="text-body3 text-gray-500">{hint}</span>}
      </div>
      <div className="mt-1.5">{children}</div>
    </div>
  )
}

function SummaryTerm({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-md bg-muted/60 p-3">
      <dt className="text-body3 text-gray-500">{label}</dt>
      <dd className="mt-1 font-medium text-gray-900">{value}</dd>
    </div>
  )
}

async function downloadVersion(studyId: string, reportId: string, version: number) {
  const blob = await downloadStudyClinicalReport(studyId, reportId)
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = `informe-holter-v${version}.pdf`
  anchor.click()
  URL.revokeObjectURL(url)
}

function formatDate(value: string) {
  return formatDateTime(value)
}

function formatDuration(ms: number) {
  const minutes = Math.round(ms / 60_000)
  return minutes < 60 ? `${minutes} min` : `${Math.floor(minutes / 60)} h ${minutes % 60} min`
}
