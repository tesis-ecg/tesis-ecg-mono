import { BatteryLow, HeartPulse, WifiOff } from 'lucide-react'
import { Link } from 'react-router-dom'

import { cn } from '@/lib/utils'
import { formatRelativeTime } from '@/lib/time'

import { useDeviceWatchdog } from '../hooks/useDeviceWatchdog'
import type { DeviceWatchdogReason } from '../types'
import { WidgetCard } from './WidgetCard'

const REASON: Record<
  DeviceWatchdogReason,
  { label: string; icon: typeof WifiOff; className: string }
> = {
  offline: { label: 'Sin transmitir', icon: WifiOff, className: 'text-error-700' },
  low_battery: { label: 'Batería baja', icon: BatteryLow, className: 'text-warning-700' },
  poor_signal: { label: 'Señal pobre', icon: HeartPulse, className: 'text-warning-700' },
}

/** Los cortes son los mismos que usa el backend para levantar la alerta. */
function batteryTone(percent: number): string {
  if (percent <= 15) return 'bg-error-400'
  if (percent <= 35) return 'bg-warning-500'
  return 'bg-success-500'
}

/**
 * Los chalecos que necesitan una mano.
 *
 * Era una tabla de cinco columnas donde la batería era un número suelto ("12%")
 * al lado de otros cuatro datos. Un porcentaje se compara mucho más rápido como
 * barra: en la lista se ve de un vistazo cuál está por quedarse sin carga sin
 * leer un solo número.
 */
export function DeviceWatchdogCard() {
  const { data, isLoading, isError } = useDeviceWatchdog()

  return (
    <WidgetCard
      title="Chalecos a revisar"
      icon={HeartPulse}
      to="/devices"
      isLoading={isLoading}
      isError={isError}
      isEmpty={!data || data.length === 0}
      emptyTitle="Todos los chalecos están sanos"
    >
      <ul className="flex flex-col gap-1">
        {data?.map((device) => {
          const reason = REASON[device.reason]
          const ReasonIcon = reason.icon
          return (
            <li key={device.deviceId}>
              <Link
                to={`/devices/${device.deviceId}`}
                aria-label={`Abrir dispositivo ${device.serial}`}
                className="flex flex-col gap-2 rounded-lg px-2 py-2.5 transition-colors hover:bg-gray-50 focus-visible:ring-2 focus-visible:ring-primary-500 focus-visible:outline-none"
              >
                <div className="flex items-center gap-2">
                  <span className="text-body2 flex-1 truncate font-medium text-gray-900">
                    {device.serial}
                  </span>
                  <span className={cn('text-body3 flex items-center gap-1', reason.className)}>
                    <ReasonIcon className="size-3.5" aria-hidden />
                    {reason.label}
                  </span>
                </div>

                <div className="flex items-center gap-2">
                  <div
                    className="h-1.5 flex-1 overflow-hidden rounded-full bg-gray-100"
                    role="img"
                    aria-label={
                      device.batteryPercent !== null
                        ? `Batería ${device.batteryPercent}%`
                        : 'Batería sin reportar'
                    }
                  >
                    {device.batteryPercent !== null && (
                      <div
                        className={cn('h-full rounded-full', batteryTone(device.batteryPercent))}
                        style={{ width: `${device.batteryPercent}%` }}
                      />
                    )}
                  </div>
                  <span className="text-body3 w-9 shrink-0 text-right text-gray-700">
                    {device.batteryPercent !== null ? `${device.batteryPercent}%` : '—'}
                  </span>
                  <span className="text-body3 w-24 shrink-0 text-right text-gray-500">
                    {formatRelativeTime(device.lastSeenAt)}
                  </span>
                </div>
              </Link>
            </li>
          )
        })}
      </ul>
    </WidgetCard>
  )
}
