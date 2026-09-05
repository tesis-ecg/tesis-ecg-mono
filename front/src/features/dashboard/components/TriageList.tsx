import { AlertTriangle, ChevronRight } from 'lucide-react'
import { Link } from 'react-router-dom'

import { formatRelativeTime } from '@/lib/time'

import { SEVERITY_META } from '../chartMeta'
import type { DashboardAlert } from '../types'
import { WidgetCard } from './WidgetCard'

/**
 * El backend puede mandar un `kind` que este mapa todavía no conoce (los
 * `close_reason_*` del firmware, o lo que agregue el pipeline de ML cuando
 * exista). Sin fallback la fila quedaba sin subtítulo: se veía el nombre del
 * paciente y un renglón vacío donde debía decir qué se encontró.
 */
const KIND_LABEL: Record<string, string> = {
  tachycardia: 'Taquicardia',
  bradycardia: 'Bradicardia',
  afib: 'Fibrilación auricular',
  pvc: 'Extrasístole (PVC)',
  pause: 'Pausa',
  noise: 'Ruido / artefacto',
  symptom_marker: 'Síntoma marcado por el paciente',
  other: 'Hallazgo',
  device_offline: 'Dispositivo sin transmitir',
}

interface TriageListProps {
  alerts: DashboardAlert[] | undefined
  isLoading: boolean
  isError: boolean
}

/**
 * La cola de revisión: lo primero que el médico tiene que mirar hoy.
 *
 * Era una tabla de cuatro columnas con la severidad en una pastilla. Una tabla
 * es para comparar filas entre sí, y acá no se compara nada: se atiende de
 * arriba hacia abajo. Ahora cada aviso es una fila alta con la severidad como
 * franja de color a la izquierda —la misma escala del visor de ECG— para que la
 * urgencia se lea en el barrido vertical, antes que ninguna palabra.
 */
export function TriageList({ alerts, isLoading, isError }: TriageListProps) {
  return (
    <WidgetCard
      title="Cola de revisión"
      icon={AlertTriangle}
      to="/alerts"
      isLoading={isLoading}
      isError={isError}
      isEmpty={!alerts || alerts.length === 0}
      emptyTitle="No hay nada esperando revisión"
    >
      <ul className="grid grid-cols-1 gap-x-4 gap-y-1.5 lg:grid-cols-2">
        {alerts?.map((alert) => {
          const meta = SEVERITY_META[alert.severity]
          // Una alerta de dispositivo no tiene traza que abrir: va a la ficha
          // del paciente, que es donde se ve el estado del equipo.
          const to = alert.studyId ? `/studies/${alert.studyId}` : `/patients/${alert.patientId}`
          return (
            <li key={alert.id}>
              <Link
                to={to}
                aria-label={`Abrir alerta de ${alert.patientName}`}
                className="flex items-center gap-3 rounded-lg py-2.5 pr-2 pl-3 transition-colors hover:bg-gray-50 focus-visible:ring-2 focus-visible:ring-primary-500 focus-visible:outline-none"
              >
                <span
                  className="h-9 w-1 shrink-0 rounded-full"
                  style={{ backgroundColor: meta.color }}
                  aria-hidden
                />
                <div className="flex min-w-0 flex-1 flex-col">
                  <span className="text-body2 truncate font-medium text-gray-900">
                    {alert.patientName}
                  </span>
                  <span className="text-body3 truncate text-gray-600">
                    {KIND_LABEL[alert.kind] ?? KIND_LABEL.other}
                  </span>
                </div>
                <div className="flex shrink-0 flex-col items-end">
                  <span className="text-body3 font-medium" style={{ color: meta.color }}>
                    {meta.label}
                  </span>
                  <span className="text-body3 text-gray-500">
                    {formatRelativeTime(alert.detectedAt)}
                  </span>
                </div>
                <ChevronRight className="size-4 shrink-0 text-gray-400" aria-hidden />
              </Link>
            </li>
          )
        })}
      </ul>
    </WidgetCard>
  )
}
