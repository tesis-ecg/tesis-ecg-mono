import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'

import { weekdayLabel } from '../chartMeta'
import type { ActivityPoint } from '../types'

interface WeeklyActivityChartProps {
  days: ActivityPoint[]
}

/**
 * La semana en dos barras por día: qué detectó el sistema y qué contestó el paciente.
 *
 * Puestas juntas contestan la pregunta que ningún listado contesta: si los
 * pacientes están respondiendo los avisos. Una columna de detecciones sin
 * respuestas al lado es un estudio que va a llegar sin contexto clínico, y eso
 * se ve acá antes que en ningún otro lado.
 */
export function WeeklyActivityChart({ days }: WeeklyActivityChartProps) {
  const data = days.map((day) => ({
    day: weekdayLabel(day.date),
    date: day.date,
    Detectados: day.alerts,
    Respondidos: day.reports,
  }))
  const todayIndex = data.length - 1

  return (
    <div className="h-56 w-full">
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={data} margin={{ top: 8, right: 8, bottom: 0, left: -20 }}>
          <CartesianGrid vertical={false} stroke="var(--color-gray-100)" />
          <XAxis
            dataKey="day"
            tickLine={false}
            axisLine={false}
            tick={{ fill: 'var(--color-gray-600)', fontSize: 12 }}
          />
          <YAxis
            allowDecimals={false}
            tickLine={false}
            axisLine={false}
            tick={{ fill: 'var(--color-gray-600)', fontSize: 12 }}
            width={40}
          />
          <Tooltip
            cursor={{ fill: 'var(--color-gray-50)' }}
            contentStyle={{
              borderRadius: 'var(--radius-lg)',
              border: '1px solid var(--color-border)',
              boxShadow: 'var(--shadow-md)',
              fontSize: 12,
            }}
          />
          <Legend
            verticalAlign="top"
            align="right"
            height={28}
            iconType="circle"
            wrapperStyle={{ fontSize: 12 }}
            // Recharts pinta el texto de la leyenda del color de la serie: el
            // verde de las barras quedaba ilegible como tipografía, y el navy
            // competía con el título de la card.
            formatter={(value) => <span style={{ color: 'var(--color-gray-700)' }}>{value}</span>}
          />
          {/* El `fill` de la barra existe para la leyenda: las `Cell` de abajo lo
              pisan barra por barra, pero sin él el ícono de la leyenda sale negro. */}
          <Bar
            dataKey="Detectados"
            fill="var(--color-primary-500)"
            radius={[4, 4, 0, 0]}
            isAnimationActive={false}
          >
            {data.map((_, index) => (
              <Cell
                key={index}
                // Hoy en el navy pleno; los días anteriores, atenuados. Sin esto
                // hay que contar barras desde la izquierda para ubicarse.
                fill={
                  index === todayIndex ? 'var(--color-primary-500)' : 'var(--color-primary-200)'
                }
              />
            ))}
          </Bar>
          <Bar
            dataKey="Respondidos"
            radius={[4, 4, 0, 0]}
            fill="var(--color-success-500)"
            isAnimationActive={false}
          />
        </BarChart>
      </ResponsiveContainer>
    </div>
  )
}
