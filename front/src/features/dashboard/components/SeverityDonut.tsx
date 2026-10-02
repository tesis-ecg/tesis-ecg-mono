import { Cell, Pie, PieChart, ResponsiveContainer } from 'recharts'

import { SEVERITY_META } from '../chartMeta'
import type { SeverityBucket } from '../types'

interface SeverityDonutProps {
  buckets: SeverityBucket[]
  /**
   * Chalecos sin transmitir. No son filas de `alert` —el backend las fabrica al
   * vuelo— pero sí cuentan en el KPI de "alertas pendientes" de arriba. Sin
   * decirlo, la pantalla mostraba 43 en la tarjeta y 32 en la torta y no había
   * forma de saber cuál era el número bueno.
   */
  deviceAlerts: number
}

/**
 * Cómo se reparten las alertas sin leer.
 *
 * El número de arriba dice cuántas hay; esto dice **de qué gravedad**, que es lo
 * que decide si el médico abre la bandeja ahora o después del turno. Doce alertas
 * bajas y doce críticas se ven igual en un KPI y no son la misma mañana.
 *
 * Los colores son los del visor de ECG: el rojo de una banda crítica sobre la
 * traza y el rojo de esta torta tienen que ser el mismo rojo.
 */
export function SeverityDonut({ buckets, deviceAlerts }: SeverityDonutProps) {
  const total = buckets.reduce((sum, bucket) => sum + bucket.count, 0)
  const visible = buckets.filter((bucket) => bucket.count > 0)

  return (
    <div className="flex flex-col items-center gap-4 sm:flex-row sm:items-center">
      <div className="relative size-40 shrink-0">
        <ResponsiveContainer width="100%" height="100%">
          <PieChart>
            <Pie
              data={visible}
              dataKey="count"
              nameKey="severity"
              innerRadius="66%"
              outerRadius="100%"
              paddingAngle={visible.length > 1 ? 2 : 0}
              startAngle={90}
              endAngle={-270}
              isAnimationActive={false}
              stroke="none"
            >
              {visible.map((bucket) => (
                <Cell key={bucket.severity} fill={SEVERITY_META[bucket.severity].color} />
              ))}
            </Pie>
          </PieChart>
        </ResponsiveContainer>
        {/* El total va en el agujero: sin esto hay que sumar la leyenda de memoria. */}
        <div className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center">
          <span className="text-h5 leading-none text-gray-900">{total}</span>
          <span className="text-body3 text-gray-600">sin leer</span>
        </div>
      </div>

      <div className="flex w-full flex-col gap-2">
        <ul className="flex flex-col gap-2">
          {buckets.map((bucket) => {
            const meta = SEVERITY_META[bucket.severity]
            const share = total === 0 ? 0 : Math.round((bucket.count / total) * 100)
            return (
              <li key={bucket.severity} className="flex items-center gap-2">
                <span
                  className="size-2.5 shrink-0 rounded-full"
                  style={{ backgroundColor: meta.color }}
                  aria-hidden
                />
                <span className="text-body2 flex-1 text-gray-700">{meta.label}</span>
                <span className="text-body2 font-medium text-gray-900">{bucket.count}</span>
                <span className="text-body3 w-10 text-right text-gray-500">{share}%</span>
              </li>
            )
          })}
        </ul>
        {deviceAlerts > 0 && (
          <p className="text-body3 border-t border-border pt-2 text-gray-600">
            Y {deviceAlerts} {deviceAlerts === 1 ? 'chaleco' : 'chalecos'} sin transmitir, que
            también cuentan como pendientes.
          </p>
        )}
      </div>
    </div>
  )
}
