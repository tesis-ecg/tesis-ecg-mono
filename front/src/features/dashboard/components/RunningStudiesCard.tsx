import { Activity } from 'lucide-react'
import { useNavigate } from 'react-router-dom'

import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { formatDateTime, formatDurationMs } from '@/lib/time'

import { useRunningStudies } from '../hooks/useRunningStudies'
import { rowNavProps } from './rowNav'
import { WidgetCard } from './WidgetCard'

export function RunningStudiesCard() {
  const navigate = useNavigate()
  const { data, isLoading, isError } = useRunningStudies()

  return (
    <WidgetCard
      title="Estudios en curso"
      icon={Activity}
      to="/studies"
      isLoading={isLoading}
      isError={isError}
      isEmpty={!data || data.length === 0}
      emptyTitle="No hay estudios en curso"
    >
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Paciente</TableHead>
            <TableHead className="hidden sm:table-cell">Inicio</TableHead>
            <TableHead>Duración</TableHead>
            <TableHead className="hidden lg:table-cell">Holter</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {data?.map((s) => (
            <TableRow
              key={s.id}
              {...rowNavProps(navigate, `/studies/${s.id}`, `Abrir estudio de ${s.patientName}`)}
            >
              <TableCell className="font-medium text-gray-900">{s.patientName}</TableCell>
              <TableCell className="hidden sm:table-cell text-gray-600">
                {formatDateTime(s.startedAt)}
              </TableCell>
              <TableCell>{formatDurationMs(s.durationMs)}</TableCell>
              <TableCell className="hidden lg:table-cell text-gray-600">{s.deviceSerial}</TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </WidgetCard>
  )
}
