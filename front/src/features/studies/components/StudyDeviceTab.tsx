import { Calendar, Cpu, HeartPulse, Radio } from 'lucide-react'

import { EmptyState } from '@/components/EmptyState'
import { Spinner } from '@/components/Spinner'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { HolterHealthCard } from '@/features/devices/components/HolterHealthCard'
import { HolterStatusBadge } from '@/features/devices/components/HolterStatusBadge'
import { useHolter } from '@/features/devices/hooks/useHolter'
import { useHolterHealth } from '@/features/devices/hooks/useHolterHealth'
import { isApiError, unwrapError } from '@/lib/api'
import { formatDate, formatDateTime, formatRelativeTime } from '@/lib/time'

interface StudyDeviceTabProps {
  deviceId: string
}

/** Vista de lectura del Holter utilizado por el estudio. */
export function StudyDeviceTab({ deviceId }: StudyDeviceTabProps) {
  const holter = useHolter(deviceId)
  const health = useHolterHealth(deviceId)

  if (holter.isLoading) {
    return (
      <Card className="p-6">
        <Spinner label="Cargando dispositivo…" />
      </Card>
    )
  }

  if (holter.isError || !holter.data) {
    return (
      <Card className="p-6">
        <EmptyState
          icon={HeartPulse}
          title="No pudimos cargar el dispositivo"
          description={
            holter.error ? unwrapError(holter.error) : 'El dispositivo no está disponible.'
          }
          action={
            <Button variant="outline" onClick={() => void holter.refetch()}>
              Reintentar
            </Button>
          }
        />
      </Card>
    )
  }

  const device = holter.data
  const healthUnavailable =
    health.isError &&
    isApiError(health.error) &&
    (health.error.status === 404 || health.error.serverCode === 'DEVICE_HEALTH_NOT_FOUND')

  return (
    <div className="flex flex-col gap-4">
      <Card className="flex flex-col gap-5 p-6">
        <header className="flex flex-wrap items-start justify-between gap-3">
          <div className="flex items-start gap-3">
            <div className="flex size-11 shrink-0 items-center justify-center rounded-full bg-primary-50 text-primary-500">
              <HeartPulse className="size-5" aria-hidden />
            </div>
            <div>
              <h2 className="font-mono text-h6 text-gray-900">{device.serial}</h2>
              <p className="text-body3 text-gray-600">{device.model}</p>
            </div>
          </div>
          <HolterStatusBadge status={device.status} />
        </header>

        <dl className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
          <DeviceMetadata
            icon={Cpu}
            label="Firmware"
            value={device.firmwareVersion ? `v${device.firmwareVersion}` : 'Sin dato'}
          />
          <DeviceMetadata icon={Calendar} label="Registrado" value={formatDate(device.createdAt)} />
          <DeviceMetadata
            icon={Radio}
            label="Última conexión"
            value={device.lastSeenAt ? formatDateTime(device.lastSeenAt) : 'Nunca'}
            hint={device.lastSeenAt ? formatRelativeTime(device.lastSeenAt) : undefined}
          />
        </dl>
      </Card>

      {health.isLoading ? (
        <Card className="p-6">
          <Spinner label="Cargando telemetría…" />
        </Card>
      ) : health.data ? (
        <HolterHealthCard health={health.data} />
      ) : healthUnavailable ? (
        <Card className="p-6">
          <EmptyState
            icon={Radio}
            title="Sin telemetría disponible"
            description="El Holter todavía no informó batería, señal ni almacenamiento."
          />
        </Card>
      ) : health.isError ? (
        <Card className="p-6">
          <EmptyState
            icon={Radio}
            title="No pudimos cargar la telemetría"
            description={unwrapError(health.error)}
            action={
              <Button variant="outline" onClick={() => void health.refetch()}>
                Reintentar
              </Button>
            }
          />
        </Card>
      ) : null}
    </div>
  )
}

interface DeviceMetadataProps {
  icon: typeof Cpu
  label: string
  value: string
  hint?: string
}

function DeviceMetadata({ icon: Icon, label, value, hint }: DeviceMetadataProps) {
  return (
    <div className="flex items-start gap-2.5">
      <div className="mt-0.5 flex size-8 shrink-0 items-center justify-center rounded-md bg-primary-50 text-primary-500">
        <Icon className="size-4" aria-hidden />
      </div>
      <div>
        <dt className="text-body3 text-gray-600">{label}</dt>
        <dd className="text-body2 font-medium text-gray-900">{value}</dd>
        {hint && <dd className="text-helper text-gray-500">{hint}</dd>}
      </div>
    </div>
  )
}
