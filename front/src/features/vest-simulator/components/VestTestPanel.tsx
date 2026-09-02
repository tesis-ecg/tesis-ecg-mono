import { HeartPulse, ShieldAlert, ShieldCheck } from 'lucide-react'
import { useState } from 'react'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Label } from '@/components/ui/label'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'

import type { SimulateAnomalyBody, SimulatedAnomalyType } from '../api/simulatorApi'
import type { VestState } from '../types'

const ANOMALY_LABEL: Record<SimulatedAnomalyType, string> = {
  afib: 'Fibrilación auricular',
  tachycardia: 'Taquicardia',
  bradycardia: 'Bradicardia',
  pvc: 'Extrasístole (PVC)',
  pause: 'Pausa',
}

interface VestTestPanelProps {
  vest: VestState
  onSetPlacement: (ok: boolean) => void
  onSimulateAnomaly: (body: SimulateAnomalyBody) => void
}

/**
 * Los dos avisos que la app del paciente sabe recibir, disparables a mano.
 *
 * Está aparte del ciclo de lotes porque ninguno de los dos viaja con la señal:
 * la colocación va por el canal corto del equipo y la anomalía la produce el
 * backend. Los dos existen acá y no en `ScenarioForm` porque no son
 * configuración de la corrida sino acciones que se disparan **durante** la
 * prueba, con la app abierta al lado.
 */
export function VestTestPanel({ vest, onSetPlacement, onSimulateAnomaly }: VestTestPanelProps) {
  const [eventType, setEventType] = useState<SimulatedAnomalyType>('afib')
  const [severity, setSeverity] = useState<'high' | 'critical'>('high')

  const { config, stats } = vest
  const hasCredentials = Boolean(config.serial && config.apiKey)
  const misplaced = !config.placementOk

  return (
    <section className="flex flex-col gap-3 rounded-md border border-gray-200 bg-gray-50 p-4">
      <div className="flex items-center justify-between gap-2">
        <h4 className="text-body2 font-medium text-gray-900">Avisos al paciente</h4>
        <Badge variant={misplaced ? 'destructive' : 'success'}>
          {misplaced ? 'Mal colocado' : 'Bien colocado'}
        </Badge>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <Button
          size="sm"
          variant={misplaced ? 'outline' : 'destructive'}
          onClick={() => onSetPlacement(misplaced)}
          disabled={!hasCredentials}
        >
          {misplaced ? (
            <ShieldCheck className="mr-1 size-4" aria-hidden />
          ) : (
            <ShieldAlert className="mr-1 size-4" aria-hidden />
          )}
          {misplaced ? 'Marcar bien colocado' : 'Marcar mal colocado'}
        </Button>
        <span className="text-body3 text-gray-600">
          {hasCredentials
            ? 'Va por el canal corto del equipo, sin esperar al próximo lote.'
            : 'Elegí un equipo y rotá su API key para poder reportar.'}
        </span>
      </div>

      <div className="grid gap-3 border-t border-gray-200 pt-3 sm:grid-cols-[1fr_auto_auto] sm:items-end">
        <div className="flex flex-col gap-1">
          <Label className="text-body3">Hallazgo</Label>
          <Select
            value={eventType}
            onValueChange={(value) => setEventType(value as SimulatedAnomalyType)}
          >
            <SelectTrigger>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {Object.entries(ANOMALY_LABEL).map(([value, label]) => (
                <SelectItem key={value} value={value}>
                  {label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <div className="flex flex-col gap-1">
          <Label className="text-body3">Severidad</Label>
          <Select
            value={severity}
            onValueChange={(value) => setSeverity(value as 'high' | 'critical')}
          >
            <SelectTrigger className="sm:w-32">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {/* Solo las dos que despiertan al celular: una `low` no notifica
                  y el botón no haría nada visible. */}
              <SelectItem value="high">Alta</SelectItem>
              <SelectItem value="critical">Crítica</SelectItem>
            </SelectContent>
          </Select>
        </div>
        <Button
          size="sm"
          variant="outline"
          onClick={() => onSimulateAnomaly({ eventType, severity })}
          disabled={!stats.studyId}
          title={
            stats.studyId ? undefined : 'Hace falta señal ingerida: mandá al menos un lote antes.'
          }
        >
          <HeartPulse className="mr-1 size-4" aria-hidden />
          Simular anomalía
        </Button>
      </div>

      {!stats.studyId && (
        <p className="text-body3 text-gray-600">
          La anomalía se ancla dentro de la señal ya subida, así que la respuesta del paciente cae
          sobre el ECG. Mandá un lote primero.
        </p>
      )}
    </section>
  )
}
