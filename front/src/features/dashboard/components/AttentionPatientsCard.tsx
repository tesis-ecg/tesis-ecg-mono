import { Users } from 'lucide-react'
import { useNavigate } from 'react-router-dom'

import { PatientStatusBadge } from '@/features/patients/components/PatientStatusBadge'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { formatRelativeTime } from '@/lib/time'

import { useAttentionPatients } from '../hooks/useAttentionPatients'
import { rowNavProps } from './rowNav'
import { WidgetCard } from './WidgetCard'

export function AttentionPatientsCard() {
  const navigate = useNavigate()
  const { data, isLoading, isError } = useAttentionPatients()

  return (
    <WidgetCard
      title="Pacientes que requieren atención"
      icon={Users}
      to="/patients"
      isLoading={isLoading}
      isError={isError}
      isEmpty={!data || data.length === 0}
      emptyTitle="No hay pacientes en seguimiento"
    >
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Paciente</TableHead>
            <TableHead>Estado</TableHead>
            <TableHead className="hidden sm:table-cell">Último dato</TableHead>
            <TableHead className="hidden lg:table-cell">Dispositivo</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {data?.map((p) => (
            <TableRow
              key={p.id}
              {...rowNavProps(navigate, `/patients/${p.id}`, `Abrir paciente ${p.fullName}`)}
            >
              <TableCell className="font-medium text-gray-900">{p.fullName}</TableCell>
              <TableCell>
                <PatientStatusBadge status={p.studyStatus} />
              </TableCell>
              <TableCell className="hidden sm:table-cell text-gray-600">
                {formatRelativeTime(p.lastDataReceivedAt)}
              </TableCell>
              <TableCell className="hidden lg:table-cell text-gray-600">
                {p.deviceSerial ?? '—'}
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </WidgetCard>
  )
}
