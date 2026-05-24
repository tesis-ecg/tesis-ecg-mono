import { useNavigate } from 'react-router-dom'

import { Badge } from '@/components/ui/badge'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { formatDateTime } from '@/lib/time'

import type { PatientStudy, PatientStudySessionStatus } from '../types'

interface StudiesTableProps {
  studies: PatientStudy[]
}

const STATUS_VARIANT: Record<
  PatientStudySessionStatus,
  { label: string; variant: 'success' | 'warning' | 'info' | 'neutral' | 'destructive' }
> = {
  completed: { label: 'Completado', variant: 'info' },
  in_progress: { label: 'En curso', variant: 'success' },
  cancelled: { label: 'Cancelado', variant: 'destructive' },
  scheduled: { label: 'Programado', variant: 'warning' },
}

export function StudiesTable({ studies }: StudiesTableProps) {
  const navigate = useNavigate()
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Fecha de inicio</TableHead>
          <TableHead className="hidden md:table-cell">Duración</TableHead>
          <TableHead>Estado</TableHead>
          <TableHead className="hidden lg:table-cell">Muestras</TableHead>
          <TableHead className="hidden lg:table-cell">Eventos</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {studies.map((s) => {
          const status = STATUS_VARIANT[s.status]
          return (
            <TableRow
              key={s.id}
              onClick={() => navigate(`/studies/${s.id}`)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' || e.key === ' ') {
                  e.preventDefault()
                  navigate(`/studies/${s.id}`)
                }
              }}
              tabIndex={0}
              role="button"
              aria-label={`Abrir estudio ${s.id}`}
              className="cursor-pointer focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-primary-500 focus-visible:outline-none"
            >
              <TableCell className="font-medium text-gray-900">
                {formatDateTime(s.startedAt)}
              </TableCell>
              <TableCell className="hidden md:table-cell">
                {s.durationHours ? `${s.durationHours} h` : '—'}
              </TableCell>
              <TableCell>
                <Badge variant={status.variant}>{status.label}</Badge>
              </TableCell>
              <TableCell className="hidden lg:table-cell">
                {s.samplesCount.toLocaleString('es-AR')}
              </TableCell>
              <TableCell className="hidden lg:table-cell">{s.eventsCount}</TableCell>
            </TableRow>
          )
        })}
      </TableBody>
    </Table>
  )
}
