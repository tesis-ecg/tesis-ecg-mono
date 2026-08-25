import { Calendar, CheckCircle2, Clock, HeartPulse, XCircle } from 'lucide-react'
import { useState } from 'react'

import { KebabMenu } from '@/components/KebabMenu'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { formatDateTime, formatDurationMs } from '@/lib/time'

import { CloseStudyDialog } from './CloseStudyDialog'
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
  const [closing, setClosing] = useState<'complete' | 'cancel' | null>(null)

  // Un estudio abierto es el único que se puede terminar. `scheduled` todavía no
  // grabó nada, así que solo admite cancelarse — el backend rechaza completarlo.
  const isRunning = study.status === 'in_progress'
  const isOpen = isRunning || study.status === 'scheduled'

  return (
    <Card className="flex flex-col gap-4 p-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        {/* El inicio ya vive en la grilla de abajo y en el breadcrumb: repetirlo
            acá era ruido. El h1 solo identifica de quién es el estudio. */}
        <h1 className="text-h5 text-gray-900">Estudio · {study.patientName}</h1>
        <div className="flex flex-wrap items-center gap-2">
          <Badge variant={status.variant}>{status.label}</Badge>
          {isRunning && (
            <Button size="sm" onClick={() => setClosing('complete')}>
              <CheckCircle2 className="mr-1 size-4" aria-hidden />
              Finalizar estudio
            </Button>
          )}
          {isOpen && (
            <KebabMenu
              label={`Acciones del estudio de ${study.patientName}`}
              actions={[
                {
                  label: 'Cancelar estudio',
                  icon: XCircle,
                  variant: 'destructive',
                  onSelect: () => setClosing('cancel'),
                },
              ]}
            />
          )}
        </div>
      </div>

      {closing && (
        <CloseStudyDialog
          studyId={study.id}
          patientName={study.patientName}
          mode={closing}
          open
          onOpenChange={(open) => !open && setClosing(null)}
        />
      )}

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
