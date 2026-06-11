import { AlertTriangle } from 'lucide-react'
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
import { formatRelativeTime } from '@/lib/time'

import { useDashboardAlerts } from '../hooks/useDashboardAlerts'
import type { AlertKind, AlertSeverity } from '../types'
import { rowNavProps } from './rowNav'
import { WidgetCard } from './WidgetCard'

const SEVERITY: Record<
  AlertSeverity,
  { label: string; variant: 'destructive' | 'warning' | 'info' | 'neutral' }
> = {
  critical: { label: 'Crítica', variant: 'destructive' },
  high: { label: 'Alta', variant: 'warning' },
  medium: { label: 'Media', variant: 'info' },
  low: { label: 'Baja', variant: 'neutral' },
}

const KIND_LABEL: Record<AlertKind, string> = {
  tachycardia: 'Taquicardia',
  bradycardia: 'Bradicardia',
  afib: 'Fibrilación auricular',
  pvc: 'Extrasístole (PVC)',
  pause: 'Pausa',
  noise: 'Ruido / artefacto',
  device_offline: 'Dispositivo sin transmitir',
}

export function PendingAlertsCard() {
  const navigate = useNavigate()
  const { data, isLoading, isError } = useDashboardAlerts()

  return (
    <WidgetCard
      title="Alertas pendientes"
      icon={AlertTriangle}
      to="/patients"
      isLoading={isLoading}
      isError={isError}
      isEmpty={!data || data.length === 0}
      emptyTitle="Sin alertas pendientes"
    >
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Severidad</TableHead>
            <TableHead>Paciente</TableHead>
            <TableHead className="hidden md:table-cell">Tipo</TableHead>
            <TableHead className="hidden sm:table-cell">Detectada</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {data?.map((a) => {
            const sev = SEVERITY[a.severity]
            const to = a.studyId ? `/studies/${a.studyId}` : `/patients/${a.patientId}`
            return (
              <TableRow
                key={a.id}
                {...rowNavProps(navigate, to, `Abrir alerta de ${a.patientName}`)}
              >
                <TableCell>
                  <Badge variant={sev.variant}>{sev.label}</Badge>
                </TableCell>
                <TableCell className="font-medium text-gray-900">{a.patientName}</TableCell>
                <TableCell className="hidden md:table-cell">{KIND_LABEL[a.kind]}</TableCell>
                <TableCell className="hidden sm:table-cell text-gray-600">
                  {formatRelativeTime(a.detectedAt)}
                </TableCell>
              </TableRow>
            )
          })}
        </TableBody>
      </Table>
    </WidgetCard>
  )
}
