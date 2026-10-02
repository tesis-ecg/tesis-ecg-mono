import { Minus, TrendingDown, TrendingUp } from 'lucide-react'

import { cn } from '@/lib/utils'

import { formatTrend, readTrend, trendTone, type TrendPolarity } from '../chartMeta'
import type { ActivityTrend } from '../types'

interface TrendChipProps {
  trend: ActivityTrend
  /** Cómo se lee la variación: más alertas es malo, más estudios es bueno. */
  polarity: TrendPolarity
}

const TONE = {
  positive: 'bg-success-100 text-success-700',
  negative: 'bg-error-100 text-error-700',
  neutral: 'bg-gray-100 text-gray-700',
}

const ICON = { up: TrendingUp, down: TrendingDown, flat: Minus }

/** Píldora de variación semanal, al lado del número grande de una tarjeta. */
export function TrendChip({ trend, polarity }: TrendChipProps) {
  const reading = readTrend(trend)
  const tone = trendTone(reading.direction, polarity)
  const Icon = ICON[reading.direction]

  return (
    <span
      className={cn(
        'text-body3 inline-flex items-center gap-1 rounded-full px-2 py-0.5 font-medium',
        TONE[tone],
      )}
      title={`${trend.current} en los últimos 7 días · ${trend.previous} en los 7 anteriores`}
    >
      <Icon className="size-3" aria-hidden />
      {formatTrend(reading)}
    </span>
  )
}
