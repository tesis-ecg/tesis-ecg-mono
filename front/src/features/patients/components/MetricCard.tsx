import type { LucideIcon } from 'lucide-react'

import { Card } from '@/components/ui/card'
import { cn } from '@/lib/utils'

interface MetricCardProps {
  label: string
  value: string | number
  unit?: string
  trend?: { value: string; direction: 'up' | 'down' | 'flat' }
  icon?: LucideIcon
}

const TREND_COLOR = {
  up: 'text-success-700',
  down: 'text-error-700',
  flat: 'text-gray-600',
}

export function MetricCard({ label, value, unit, trend, icon: Icon }: MetricCardProps) {
  return (
    <Card className="flex flex-col gap-2 p-5">
      <div className="flex items-center justify-between">
        <span className="text-body3 text-gray-600">{label}</span>
        {Icon && <Icon className="size-4 text-gray-400" aria-hidden />}
      </div>
      <div className="flex items-baseline gap-1">
        <span className="text-h4 text-gray-900">{value}</span>
        {unit && <span className="text-body2 text-gray-600">{unit}</span>}
      </div>
      {trend && (
        <span className={cn('text-body3', TREND_COLOR[trend.direction])}>{trend.value}</span>
      )}
    </Card>
  )
}
