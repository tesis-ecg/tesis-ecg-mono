import { Cell, Pie, PieChart, ResponsiveContainer } from 'recharts'

import { fleetPercent } from '../chartMeta'
import type { FleetHealth } from '../types'

interface FleetGaugeProps {
  fleet: FleetHealth
  /** Compacto para la tarjeta de arriba; completo para el panel del watchdog. */
  size?: 'sm' | 'lg'
}

/** El arco cambia de color en los mismos cortes que decide el watchdog. */
function toneFor(percent: number): string {
  if (percent >= 90) return 'var(--color-success-500)'
  if (percent >= 60) return 'var(--ecg-alert-high)'
  return 'var(--ecg-alert-critical)'
}

/**
 * Cuánto de la flota está transmitiendo, en un solo arco.
 *
 * Es medio arco y no un círculo: un donut al 70% se confunde con un reparto en
 * dos categorías. El semicírculo se lee como un medidor, que es lo que es.
 */
export function FleetGauge({ fleet, size = 'sm' }: FleetGaugeProps) {
  const percent = fleetPercent(fleet.assigned, fleet.transmitting)

  if (percent === null) {
    return (
      <div className="text-body3 flex size-full items-center justify-center text-center text-gray-400">
        Sin chalecos asignados
      </div>
    )
  }

  const color = toneFor(percent)
  const data = [
    { name: 'transmitiendo', value: percent },
    { name: 'resto', value: 100 - percent },
  ]

  return (
    <div className="relative size-full">
      <ResponsiveContainer width="100%" height="100%">
        <PieChart>
          <Pie
            data={data}
            dataKey="value"
            // Semicírculo: arranca a la izquierda y termina a la derecha.
            startAngle={180}
            endAngle={0}
            cy="82%"
            innerRadius="118%"
            outerRadius="165%"
            cornerRadius={4}
            isAnimationActive={false}
            stroke="none"
          >
            <Cell fill={color} />
            <Cell fill="var(--color-gray-100)" />
          </Pie>
        </PieChart>
      </ResponsiveContainer>
      <div className="pointer-events-none absolute inset-x-0 bottom-0 flex flex-col items-center">
        <span
          className={size === 'lg' ? 'text-h5 leading-none' : 'text-body1 font-semibold'}
          style={{ color }}
        >
          {percent}%
        </span>
        {size === 'lg' && (
          <span className="text-body3 text-gray-600">
            {fleet.transmitting} de {fleet.assigned} transmitiendo
          </span>
        )}
      </div>
    </div>
  )
}
