import { Calendar, Clock, HeartPulse } from 'lucide-react'

import { Badge } from '@/components/ui/badge'
import { Card } from '@/components/ui/card'
import { formatDateTime, formatDurationMs } from '@/lib/time'

import type { PatientStudySessionStatus, Study } from '../types'

const STATUS_VARIANT: Record<
  PatientStudySessionStatus,
  { label: string; variant: 'success' | 'warning' | 'info' | 'neutral' | 'destructive' }
> = {
  completed: { label: 'Completado', variant: 'info' },
  in_progress: { label: 'En curso', variant: 'success' },
  cancelled: { label: 'Cancelado', variant: 'destructive' },
  scheduled: { label: 'Programado', variant: 'warning' },
}

interface StudyHeaderProps {
  study: Study
}

export function StudyHeader({ study }: StudyHeaderProps) {
  const status = STATUS_VARIANT[study.status]

  return (
    <Card className="flex flex-col gap-4 p-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex flex-col gap-1">
          <h1 className="text-h5 text-gray-900">Estudio · {study.patientName}</h1>
          <p className="text-body3 text-gray-600">ID: {study.id}</p>
        </div>
        <Badge variant={status.variant}>{status.label}</Badge>
      </div>

      <dl className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <Metadata icon={Calendar} label="Inicio" value={formatDateTime(study.startedAt)} />
        <Metadata
          icon={Calendar}
          label="Fin"
          value={study.endedAt ? formatDateTime(study.endedAt) : 'En curso'}
        />
        <Metadata icon={Clock} label="Duración" value={formatDurationMs(study.durationMs)} />
        <Metadata icon={HeartPulse} label="Dispositivo" value={study.deviceSerial} />
      </dl>
    </Card>
  )
}

interface MetadataProps {
  icon: typeof Calendar
  label: string
  value: string
}

function Metadata({ icon: Icon, label, value }: MetadataProps) {
  return (
    <div className="flex items-start gap-2">
      <div className="mt-0.5 flex size-8 items-center justify-center rounded-md bg-primary-50 text-primary-500">
        <Icon className="size-4" aria-hidden />
      </div>
      <div className="flex flex-col">
        <dt className="text-body3 text-gray-600">{label}</dt>
        <dd className="text-body1 font-medium text-gray-900">{value}</dd>
      </div>
    </div>
  )
}
