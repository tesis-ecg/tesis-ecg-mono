import { Bell, Clock, MessageSquareText, NotebookPen } from 'lucide-react'

import { EmptyState } from '@/components/EmptyState'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { ALERT_KIND_LABEL } from '@/features/alerts/labels'
import { formatDateTime } from '@/lib/time'
import { cn } from '@/lib/utils'

import type { StudyPatientReport } from '../types'

interface PatientReportsTableProps {
  reports: StudyPatientReport[]
  pendingSignalTotal: number
  /** Lleva el gráfico al instante del registro. Solo para los que ya tienen señal. */
  onLocate: (report: StudyPatientReport) => void
}

/** `offsetMs` → `h:mm:ss` desde el inicio de la grabación. */
function formatOffset(offsetMs: number): string {
  const total = Math.max(0, Math.floor(offsetMs / 1000))
  const hours = Math.floor(total / 3600)
  const minutes = Math.floor((total % 3600) / 60)
  const seconds = total % 60
  const mm = minutes.toString().padStart(2, '0')
  const ss = seconds.toString().padStart(2, '0')
  return hours > 0 ? `${hours}:${mm}:${ss}` : `${mm}:${ss}`
}

function symptomsText(report: StudyPatientReport): string {
  const labels = [...report.symptomLabels]
  if (report.symptomsOther) labels.push(report.symptomsOther)
  return labels.join(' · ') || '—'
}

function activityText(report: StudyPatientReport): string {
  return report.activityOther || report.activityLabel || '—'
}

/** El hallazgo que el registro responde, con su nombre clínico. */
function alertKindText(report: StudyPatientReport): string | null {
  if (report.source !== 'push_response') return null
  if (!report.alertKind) return null
  return ALERT_KIND_LABEL[report.alertKind] ?? 'Hallazgo'
}

export function PatientReportsTable({
  reports,
  pendingSignalTotal,
  onLocate,
}: PatientReportsTableProps) {
  if (reports.length === 0) {
    return (
      <EmptyState
        icon={NotebookPen}
        title="Sin registros del paciente"
        description="Acá aparecen los síntomas que el paciente carga desde la app, con lo que estaba haciendo en ese momento."
      />
    )
  }

  return (
    <div className="flex flex-col gap-3">
      {pendingSignalTotal > 0 && (
        <p className="text-body3 rounded-lg border border-info-300 bg-info-100 px-4 py-3 text-info-700">
          {pendingSignalTotal === 1
            ? 'Un registro todavía no se puede ubicar en el gráfico'
            : `${pendingSignalTotal} registros todavía no se pueden ubicar en el gráfico`}
          : el chaleco envía la señal por lotes y ese tramo aún no llegó. Van a aparecer marcados
          sobre el ECG en cuanto se reciba.
        </p>
      )}

      <div className="overflow-x-auto">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Momento</TableHead>
              <TableHead>Síntomas</TableHead>
              <TableHead className="hidden md:table-cell">Actividad</TableHead>
              <TableHead className="hidden lg:table-cell">Nota</TableHead>
              <TableHead>Origen</TableHead>
              <TableHead className="w-32">
                <span className="sr-only">Acciones</span>
              </TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {reports.map((report) => {
              const alertKind = alertKindText(report)
              return (
                <TableRow key={report.id} className={cn(!report.visibleInChart && 'bg-gray-50')}>
                  <TableCell className="whitespace-nowrap">
                    <div className="flex flex-col">
                      <span className="font-medium text-gray-900">
                        {formatDateTime(report.occurredAt)}
                      </span>
                      {report.offsetMs !== null && (
                        <span className="text-helper font-mono text-gray-500">
                          {formatOffset(report.offsetMs)} del estudio
                        </span>
                      )}
                      {/* La respuesta a un aviso se marca sobre la banda del
                        hallazgo, no en la hora en que el paciente contestó:
                        decirlo evita que los dos tiempos parezcan un error. */}
                      {alertKind !== null && (
                        <span className="text-helper text-gray-500">marcado sobre el hallazgo</span>
                      )}
                    </div>
                  </TableCell>
                  <TableCell className="max-w-64">{symptomsText(report)}</TableCell>
                  <TableCell className="hidden md:table-cell">{activityText(report)}</TableCell>
                  <TableCell className="hidden max-w-72 lg:table-cell">
                    <span className="text-gray-600">{report.notes ?? '—'}</span>
                  </TableCell>
                  <TableCell>
                    <Badge variant={report.source === 'push_response' ? 'info' : 'neutral'}>
                      {report.source === 'push_response' ? (
                        <>
                          <Bell className="size-3" aria-hidden />
                          {/* Con el hallazgo adentro: "Por aviso" a secas no
                            decía a qué aviso estaba contestando el paciente. */}
                          {alertKind ? `Aviso · ${alertKind}` : 'Por aviso'}
                        </>
                      ) : (
                        <>
                          <MessageSquareText className="size-3" aria-hidden />
                          Espontáneo
                        </>
                      )}
                    </Badge>
                  </TableCell>
                  <TableCell>
                    {report.visibleInChart ? (
                      <Button variant="ghost" size="sm" onClick={() => onLocate(report)}>
                        Ver en el ECG
                      </Button>
                    ) : (
                      <span className="text-helper flex items-center gap-1 text-gray-500">
                        <Clock className="size-3.5" aria-hidden />
                        Aún sin señal
                      </span>
                    )}
                  </TableCell>
                </TableRow>
              )
            })}
          </TableBody>
        </Table>
      </div>
    </div>
  )
}
