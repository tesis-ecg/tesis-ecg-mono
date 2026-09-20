import { useState } from 'react'
import type { ReactNode } from 'react'
import {
  CircleAlert,
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
  StudyClinicalReportPreview,
} from '@/features/studies/types'
import { isApiError, unwrapError } from '@/lib/api'

interface StudyClinicalReportTabProps {
  study: Study
  onPreview: () => void
}

export function StudyClinicalReportTab({ study, onPreview }: StudyClinicalReportTabProps) {
  const draftQ = useStudyClinicalReportDraft(study.id)
  const previewQ = useStudyClinicalReportPreview(study.id)
  const versionsQ = useStudyClinicalReportVersions(study.id)

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
        previewState={previewQ.isLoading ? 'loading' : previewQ.isError ? 'error' : 'ready'}
        onReload={() => void draftQ.refetch()}
        onPreview={onPreview}
      />
      <div className="flex flex-col gap-4">
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
  previewState: 'loading' | 'error' | 'ready'
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

  return (
    <Card className="flex flex-col gap-6 p-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-h5 text-gray-900">Datos clínicos e interpretación</h2>
          <p className="mt-1 text-sm text-gray-500">
            Podés guardar el borrador durante el estudio. Indicación y conclusión son obligatorias
            para finalizar.
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

      <ReportReadiness preview={reportPreview} state={previewState} />

      <div className="grid gap-4 md:grid-cols-2">
        <Field
          label="Indicación del estudio *"
          htmlFor="report-indication"
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
          label="Conclusión / interpretación final *"
          htmlFor="report-conclusion"
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

function ReportReadiness({
  preview,
  state,
}: {
  preview: StudyClinicalReportPreview | undefined
  state: 'loading' | 'error' | 'ready'
}) {
  if (state === 'loading') {
    return <p className="text-sm text-muted-foreground">Verificando requisitos del informe…</p>
  }
  if (state === 'error' || !preview) {
    return (
      <div className="rounded-md border border-destructive/30 bg-destructive/10 p-3 text-sm text-destructive">
        <p className="flex items-center gap-2 font-medium">
          <CircleAlert className="size-4" aria-hidden />
          No se pudo verificar si el informe puede generarse.
        </p>
      </div>
    )
  }
  const blocking = preview.issues.filter((issue) => issue.severity === 'blocking')
  const warnings = preview.issues.filter((issue) => issue.severity === 'warning')
  if (blocking.length === 0 && warnings.length === 0) return null

  return (
    <div className="grid gap-2">
      {blocking.length > 0 && (
        <div className="rounded-md border border-destructive/30 bg-destructive/10 p-3 text-sm text-destructive">
          <p className="flex items-center gap-2 font-medium">
            <CircleAlert className="size-4" aria-hidden />
            Faltan datos para generar el informe final
          </p>
          <ul className="mt-1 list-disc pl-6">
            {blocking.map((issue) => (
              <li key={issue.code}>{issue.message}</li>
            ))}
          </ul>
        </div>
      )}
      {warnings.length > 0 && (
        <div className="rounded-md border border-amber-300 bg-amber-50 p-3 text-sm text-amber-900">
          <p className="flex items-center gap-2 font-medium">
            <TriangleAlert className="size-4" aria-hidden />
            Advertencias que no bloquean la generación
          </p>
          <ul className="mt-1 list-disc pl-6">
            {warnings.map((issue) => (
              <li key={issue.code}>{issue.message}</li>
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}

function Field({
  label,
  htmlFor,
  className,
  children,
}: {
  label: string
  htmlFor: string
  className?: string
  children: ReactNode
}) {
  return (
    <div className={className}>
      <Label htmlFor={htmlFor}>{label}</Label>
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
  return new Intl.DateTimeFormat('es-AR', { dateStyle: 'short', timeStyle: 'short' }).format(
    new Date(value),
  )
}

function formatDuration(ms: number) {
  const minutes = Math.round(ms / 60_000)
  return minutes < 60 ? `${minutes} min` : `${Math.floor(minutes / 60)} h ${minutes % 60} min`
}
