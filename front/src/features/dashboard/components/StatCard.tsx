import type { LucideIcon } from 'lucide-react'
import { Link } from 'react-router-dom'
import { Area, AreaChart, Bar, BarChart, Cell, ResponsiveContainer } from 'recharts'

import { Card } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'

import type { ActivityPoint, ActivityTrend } from '../types'
import { TrendChip } from './TrendChip'
import type { TrendPolarity } from '../chartMeta'

interface StatCardProps {
  label: string
  /** Qué mide el número. Va abajo del título, en gris. */
  hint: string
  value: number | string
  icon: LucideIcon
  /** Destino del click. La tarjeta entera es el link. */
  to: string
  isLoading: boolean
  trend?: ActivityTrend
  polarity?: TrendPolarity
  /** Serie de 7 días para el mini-gráfico. La clave se elige con `metric`. */
  days?: ActivityPoint[]
  metric?: keyof Pick<ActivityPoint, 'alerts' | 'reports' | 'studies'>
  variant?: 'bars' | 'area'
  /** Reemplaza al mini-gráfico cuando el dato no es una serie (el medidor). */
  visual?: React.ReactNode
  /**
   * Línea de apoyo debajo del número, para las tarjetas sin serie diaria.
   *
   * "Pacientes activos" es un stock y no tiene serie: dibujarle al lado las
   * barras de otra métrica hacía que el número y el gráfico hablaran de cosas
   * distintas, que es exactamente lo que un dashboard clínico no puede hacer.
   */
  footnote?: string
}

/**
 * La tarjeta de arriba de la home.
 *
 * Tres cosas y en este orden: qué mide, cuánto vale hoy y cómo viene. El
 * mini-gráfico no está para leer valores —no tiene ejes ni tooltip a propósito—
 * sino para que la forma de la semana se vea sin entrar a ningún listado.
 *
 * Es un `Link` completo y no una card con un botón adentro: el médico que ve
 * "12 alertas pendientes" va a querer ir a verlas, y hacerle buscar dónde
 * clickear es fricción sin ningún beneficio.
 */
export function StatCard({
  label,
  hint,
  value,
  icon: Icon,
  to,
  isLoading,
  trend,
  polarity = 'neutral',
  days,
  metric = 'alerts',
  variant = 'bars',
  visual,
  footnote,
}: StatCardProps) {
  return (
    <Card className="transition-shadow hover:shadow-md">
      <Link
        to={to}
        className="flex h-full flex-col gap-3 rounded-xl p-5 focus-visible:ring-2 focus-visible:ring-primary-500 focus-visible:outline-none"
      >
        <div className="flex items-start justify-between gap-2">
          <div className="flex flex-col">
            <span className="text-body2 font-medium text-gray-900">{label}</span>
            <span className="text-body3 text-gray-600">{hint}</span>
          </div>
          <span className="flex size-9 shrink-0 items-center justify-center rounded-full bg-primary-50 text-primary-500">
            <Icon className="size-4" aria-hidden />
          </span>
        </div>

        <div className="flex items-end justify-between gap-3">
          <div className="flex flex-col gap-1.5">
            {isLoading ? (
              <Skeleton className="h-9 w-16" />
            ) : (
              <span className="text-h4 leading-none text-gray-900">{value}</span>
            )}
            {trend && !isLoading && <TrendChip trend={trend} polarity={polarity} />}
            {footnote && !isLoading && <span className="text-body3 text-gray-600">{footnote}</span>}
          </div>

          {(visual || days) && (
            <div className="h-12 w-24 shrink-0">
              {isLoading ? (
                <Skeleton className="size-full" />
              ) : (
                (visual ?? <MiniChart days={days ?? []} metric={metric} variant={variant} />)
              )}
            </div>
          )}
        </div>
      </Link>
    </Card>
  )
}

interface MiniChartProps {
  days: ActivityPoint[]
  metric: keyof Pick<ActivityPoint, 'alerts' | 'reports' | 'studies'>
  variant: 'bars' | 'area'
}

function MiniChart({ days, metric, variant }: MiniChartProps) {
  // Una serie toda en cero dibuja una línea pegada al piso que se lee como un
  // gráfico roto. Mejor no dibujar nada y decirlo.
  if (days.every((day) => day[metric] === 0)) {
    return (
      <div className="text-body3 flex size-full items-center justify-end text-gray-400">
        Sin movimiento
      </div>
    )
  }

  const data = days.map((day) => ({ value: day[metric] }))
  const gradientId = `stat-area-${metric}`

  if (variant === 'area') {
    return (
      <ResponsiveContainer width="100%" height="100%">
        <AreaChart data={data} margin={{ top: 2, right: 0, bottom: 0, left: 0 }}>
          <defs>
            <linearGradient id={gradientId} x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="var(--color-primary-300)" stopOpacity={0.35} />
              <stop offset="100%" stopColor="var(--color-primary-300)" stopOpacity={0} />
            </linearGradient>
          </defs>
          <Area
            type="monotone"
            dataKey="value"
            stroke="var(--color-primary-400)"
            strokeWidth={2}
            fill={`url(#${gradientId})`}
            isAnimationActive={false}
            dot={false}
          />
        </AreaChart>
      </ResponsiveContainer>
    )
  }

  return (
    <ResponsiveContainer width="100%" height="100%">
      <BarChart data={data} margin={{ top: 2, right: 0, bottom: 0, left: 0 }} barCategoryGap={3}>
        {/* `minPointSize`: un día en cero dibujaba una barra de alto cero, o sea
            nada, y la serie parecía tener tres días en vez de siete. */}
        <Bar dataKey="value" radius={[3, 3, 3, 3]} minPointSize={2} isAnimationActive={false}>
          {data.map((_, index) => (
            <Cell
              key={index}
              // La última barra es hoy: el resto de la semana es contexto.
              fill={
                index === data.length - 1 ? 'var(--color-primary-500)' : 'var(--color-primary-100)'
              }
            />
          ))}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  )
}
