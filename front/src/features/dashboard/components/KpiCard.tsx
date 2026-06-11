import type { LucideIcon } from 'lucide-react'

import { Card } from '@/components/ui/card'
import { cn } from '@/lib/utils'

import type { KpiDelta } from '../types'

interface KpiCardProps {
  label: string
  value: string | number
  icon?: LucideIcon
  delta?: KpiDelta
}

const TREND_COLOR = {
  up: 'text-success-700',
  down: 'text-error-700',
  flat: 'text-gray-600',
}

function formatDelta(delta: KpiDelta): string {
  const sign = delta.value > 0 ? '+' : ''
  return `${sign}${delta.value} vs. período anterior`
}

export function KpiCard({ label, value, icon: Icon, delta }: KpiCardProps) {
  return (
    <Card className="flex flex-col gap-2 p-5">
      <div className="flex items-center justify-between">
        <span className="text-body3 text-gray-600">{label}</span>
        {Icon && <Icon className="size-4 text-gray-400" aria-hidden />}
      </div>
      <span className="text-h4 text-gray-900">{value}</span>
      {delta && (
        <span className={cn('text-body3', TREND_COLOR[delta.trend])}>{formatDelta(delta)}</span>
      )}
    </Card>
  )
}
