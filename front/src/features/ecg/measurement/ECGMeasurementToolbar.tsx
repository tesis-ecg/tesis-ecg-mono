import { ChartArea, Eraser, Activity, MoveHorizontal, MoveVertical } from 'lucide-react'
import type { ComponentType } from 'react'

import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'

import type { MeasurementTool } from './measure'

export interface ECGMeasurementToolbarProps {
  tool: MeasurementTool | null
  onToolChange: (tool: MeasurementTool | null) => void
  /** `false` cuando el visor no dibuja muestras reales (zoom fuera del límite de detalle). */
  enabled: boolean
  disabledReason?: string
  measurementCount: number
  onClear: () => void
  viewKind?: 'raw' | 'filtered_visualization'
}

const TOOLS: {
  id: MeasurementTool
  label: string
  hint: string
  Icon: ComponentType<{ className?: string }>
}[] = [
  {
    id: 'time',
    label: 'Calibre de tiempo',
    hint: 'Arrastrá sobre la traza para medir duración y frecuencia equivalente',
    Icon: MoveHorizontal,
  },
  {
    id: 'amplitude',
    label: 'Calibre de amplitud',
    hint: 'Arrastrá entre dos puntos para medir la diferencia de voltaje entre las muestras',
    Icon: MoveVertical,
  },
  {
    id: 'area',
    label: 'Área',
    hint: 'Arrastrá para integrar el área entre la traza y la línea que une los extremos',
    Icon: ChartArea,
  },
  {
    id: 'rr',
    label: 'Compás R-R',
    hint: 'Marcá dos latidos consecutivos: el compás replica el intervalo sobre el trazado',
    Icon: Activity,
  },
]

/**
 * Selector de herramientas de medición sobre el trazado: calibres de tiempo y
 * amplitud, área y compás R-R. Stateless — el padre guarda la herramienta
 * activa (ver `useMeasurements`).
 *
 * Se deshabilita entero cuando el visor dibuja la envolvente y no las muestras:
 * medir sobre la envolvente daría números que no son de la señal.
 */
export function ECGMeasurementToolbar({
  tool,
  onToolChange,
  enabled,
  disabledReason,
  measurementCount,
  onClear,
  viewKind,
}: ECGMeasurementToolbarProps) {
  const active = TOOLS.find((t) => t.id === tool)

  return (
    <div className="flex flex-col gap-1">
      <div role="toolbar" aria-label="Herramientas de medición" className="flex items-center gap-1">
        {TOOLS.map(({ id, label, hint, Icon }) => {
          const pressed = tool === id
          return (
            <Button
              key={id}
              type="button"
              variant={pressed ? 'default' : 'outline'}
              size="icon"
              className="size-8"
              disabled={!enabled}
              aria-pressed={pressed}
              aria-label={label}
              title={`${label}. ${hint}`}
              onClick={() => onToolChange(pressed ? null : id)}
            >
              <Icon className="size-4" />
            </Button>
          )
        })}
        <Button
          type="button"
          variant="ghost"
          size="sm"
          className={cn('gap-1', measurementCount === 0 && 'invisible')}
          disabled={measurementCount === 0}
          onClick={onClear}
          aria-label={`Borrar mediciones (${measurementCount})`}
          title="Borrar todas las mediciones"
        >
          <Eraser className="size-4" />
          <span className="tabular-nums">{measurementCount}</span>
        </Button>
      </div>

      {!enabled && disabledReason ? (
        <p className="max-w-xs rounded bg-background/80 px-1.5 py-0.5 text-xs text-fg-muted">
          {disabledReason}
        </p>
      ) : active ? (
        <p className="max-w-xs text-xs text-fg-muted">{active.hint}. Esc para salir.</p>
      ) : null}

      {viewKind === 'filtered_visualization' ? (
        <p role="note" className="max-w-xs text-xs text-fg-muted">
          El trazado es el filtro de visualización (0,05–40 Hz): sirve para medir intervalos, no la
          amplitud del QRS.
        </p>
      ) : null}
    </div>
  )
}
