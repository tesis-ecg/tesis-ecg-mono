import { HeartPulse } from 'lucide-react'
import { useNavigate } from 'react-router-dom'

import { HolterStatusBadge } from '@/features/devices/components/HolterStatusBadge'
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

import { useDeviceWatchdog } from '../hooks/useDeviceWatchdog'
import type { DeviceWatchdogReason } from '../types'
import { rowNavProps } from './rowNav'
import { WidgetCard } from './WidgetCard'

const REASON: Record<DeviceWatchdogReason, { label: string; variant: 'destructive' | 'warning' }> =
  {
    offline: { label: 'Sin transmitir', variant: 'destructive' },
    low_battery: { label: 'Batería baja', variant: 'warning' },
    poor_signal: { label: 'Señal pobre', variant: 'warning' },
  }

export function DeviceWatchdogCard() {
  const navigate = useNavigate()
  const { data, isLoading, isError } = useDeviceWatchdog()

  return (
    <WidgetCard
      title="Watchdog de dispositivos"
      icon={HeartPulse}
      to="/devices"
      isLoading={isLoading}
      isError={isError}
      isEmpty={!data || data.length === 0}
      emptyTitle="Todos los dispositivos están sanos"
    >
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Holter</TableHead>
            <TableHead className="hidden md:table-cell">Estado</TableHead>
            <TableHead>Batería</TableHead>
            <TableHead className="hidden sm:table-cell">Última conexión</TableHead>
            <TableHead>Motivo</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {data?.map((d) => {
            const reason = REASON[d.reason]
            return (
              <TableRow
                key={d.deviceId}
                {...rowNavProps(
                  navigate,
                  `/devices/${d.deviceId}`,
                  `Abrir dispositivo ${d.serial}`,
                )}
              >
                <TableCell className="font-medium text-gray-900">{d.serial}</TableCell>
                <TableCell className="hidden md:table-cell">
                  <HolterStatusBadge status={d.status} />
                </TableCell>
                <TableCell className="text-gray-600">
                  {d.batteryPercent !== null ? `${d.batteryPercent}%` : '—'}
                </TableCell>
                <TableCell className="hidden sm:table-cell text-gray-600">
                  {formatRelativeTime(d.lastSeenAt)}
                </TableCell>
                <TableCell>
                  <Badge variant={reason.variant}>{reason.label}</Badge>
                </TableCell>
              </TableRow>
            )
          })}
        </TableBody>
      </Table>
    </WidgetCard>
  )
}
