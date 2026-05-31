import { Battery, Cpu, HardDrive, Radio, Upload } from 'lucide-react'

import { Badge } from '@/components/ui/badge'
import { Card } from '@/components/ui/card'
import { formatDateTime, formatRelativeTime } from '@/lib/time'
import { cn } from '@/lib/utils'

import type { HolterHealth, HolterSignalQuality } from '../types'

interface HolterHealthCardProps {
  health: HolterHealth
}

function batteryVariant(percent: number): 'success' | 'warning' | 'destructive' {
  if (percent >= 50) return 'success'
  if (percent >= 20) return 'warning'
  return 'destructive'
}

function signalVariant(q: HolterSignalQuality): 'success' | 'warning' | 'destructive' | 'neutral' {
  if (q === 'good') return 'success'
  if (q === 'fair') return 'warning'
  if (q === 'poor') return 'destructive'
  return 'neutral'
}

const SIGNAL_LABEL: Record<HolterSignalQuality, string> = {
  good: 'Buena',
  fair: 'Regular',
  poor: 'Pobre',
  none: 'Sin señal',
}

export function HolterHealthCard({ health }: HolterHealthCardProps) {
  const storagePct = Math.round((health.storageUsedMb / health.storageTotalMb) * 100)
  return (
    <Card className="flex flex-col gap-5 p-6">
      <header className="flex flex-col gap-1">
        <div className="flex items-center justify-between">
          <h3 className="text-h6 text-gray-900">{health.serial}</h3>
          <Badge variant="outline" className="font-mono">
            {health.model}
          </Badge>
        </div>
        <p className="text-body3 text-gray-600">Firmware v{health.firmwareVersion}</p>
      </header>

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <Row
          icon={Battery}
          label="Batería"
          value={`${health.batteryPercent}%`}
          badge={
            <Badge variant={batteryVariant(health.batteryPercent)}>
              {health.batteryPercent >= 50
                ? 'OK'
                : health.batteryPercent >= 20
                  ? 'Baja'
                  : 'Crítica'}
            </Badge>
          }
        />
        <Row
          icon={Radio}
          label="Señal celular"
          value={`${health.signalDbm} dBm`}
          badge={
            <Badge variant={signalVariant(health.signalQuality)}>
              {SIGNAL_LABEL[health.signalQuality]}
            </Badge>
          }
        />
        <Row icon={Cpu} label="Último ping" value={formatRelativeTime(health.lastPingAt)} />
        <Row
          icon={Upload}
          label="Próximo envío"
          value={formatDateTime(health.nextScheduledUploadAt)}
          hint={`${health.uploadsToday} envíos hoy`}
        />
        <Row
          icon={HardDrive}
          label="Almacenamiento"
          value={`${health.storageUsedMb} / ${health.storageTotalMb} MB`}
          hint={`${storagePct}% usado`}
        />
      </div>
    </Card>
  )
}

interface RowProps {
  icon: typeof Battery
  label: string
  value: string
  badge?: React.ReactNode
  hint?: string
}

function Row({ icon: Icon, label, value, badge, hint }: RowProps) {
  return (
    <div className={cn('flex items-start gap-3')}>
      <div className="mt-0.5 flex size-9 shrink-0 items-center justify-center rounded-full bg-primary-50 text-primary-500">
        <Icon className="size-4" aria-hidden />
      </div>
      <div className="flex flex-col">
        <span className="text-body3 text-gray-600">{label}</span>
        <div className="flex items-center gap-2">
          <span className="text-body2 font-medium text-gray-900">{value}</span>
          {badge}
        </div>
        {hint && <span className="text-helper text-gray-500">{hint}</span>}
      </div>
    </div>
  )
}
