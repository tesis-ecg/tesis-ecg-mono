import { Activity, HeartPulse, ShieldAlert, ShieldCheck } from 'lucide-react'
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
  onInjectAnomaly: (type: SimulatedAnomalyType) => void
}

/**
 * Acciones que se disparan **durante** la prueba, con la app abierta al lado.
 *
 * - La colocación va por el canal corto del equipo. El equipo también la manda
 *   solo cuando la señal grabada tiene un electrodo suelto o mala calidad
 *   sostenidos; esto es el atajo manual.
 * - Una arritmia se puede **inyectar** en el próximo lote, y entonces está en el
 *   trazado y la mide el backend; o **simular** del lado del backend sobre la
 *   señal ya subida, que es lo rápido para probar la notificación al paciente.
 */
export function VestTestPanel({
  vest,
  onSetPlacement,
  onSimulateAnomaly,
  onInjectAnomaly,
}: VestTestPanelProps) {
  const [eventType, setEventType] = useState<SimulatedAnomalyType>('afib')
  const [severity, setSeverity] = useState<'high' | 'critical'>('high')

  const { config, stats } = vest
  const hasCredentials = Boolean(config.serial && config.apiKey)
  const misplaced = !config.placementOk

  return (
    <section className="flex flex-col gap-3 rounded-md border border-gray-200 bg-gray-50 p-4">
      <div className="flex items-center justify-between gap-2">
        <h4 className="text-body2 font-medium text-gray-900">Avisos y arritmias</h4>
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

      <div className="grid gap-3 border-t border-gray-200 pt-3 sm:grid-cols-[1fr_auto] sm:items-end">
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
        <div className="flex flex-wrap gap-2 sm:col-span-2">
          <Button
            size="sm"
            onClick={() => onInjectAnomaly(eventType)}
            title="La arritmia queda grabada en el ECG del próximo lote que se genere."
          >
            <Activity className="mr-1 size-4" aria-hidden />
            Inyectar en el próximo lote
          </Button>
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
            Simular hallazgo en el backend
          </Button>
        </div>
      </div>

      {config.pendingInjections.length > 0 && (
        <p className="text-body3 text-primary-500">
          {config.pendingInjections.length} arritmia(s) esperando el próximo lote.
        </p>
      )}
      <p className="text-body3 text-gray-600">
        Inyectar la pone en la señal: se ve en el trazado y la mide el backend. Simular fabrica el
        hallazgo sobre la señal ya subida (la severidad aplica solo acá), para probar la
        notificación al paciente.
      </p>
    </section>
  )
}
