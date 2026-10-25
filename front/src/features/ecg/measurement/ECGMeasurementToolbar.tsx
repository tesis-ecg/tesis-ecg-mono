import { ChartArea, Eraser, Activity, MoveHorizontal, MoveVertical, Hand } from 'lucide-react'
import { useId, type ComponentType } from 'react'

import { Button } from '@/components/ui/button'
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip'

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

/** Selección del modo de interacción: mano para desplazar o una herramienta para medir. */
export function ECGMeasurementToolbar({
  tool,
  onToolChange,
  enabled,
  disabledReason,
  measurementCount,
  onClear,
  viewKind,
}: ECGMeasurementToolbarProps) {
  const helpId = useId()
  const filteredHelp =
    viewKind === 'filtered_visualization'
      ? 'El trazado está filtrado para visualización (0,05–40 Hz): sirve para medir intervalos, no la amplitud del QRS.'
      : ''
  const availabilityHelp = !enabled ? disabledReason : ''
  const help = [availabilityHelp, filteredHelp].filter(Boolean).join(' ')

  return (
    <TooltipProvider delayDuration={250}>
      <div
        role="group"
        aria-label="Herramientas del gráfico"
        className="flex min-w-0 flex-wrap items-center gap-1 p-1"
      >
        <Tooltip>
          <TooltipTrigger asChild>
            <Button
              type="button"
              variant={tool === null ? 'default' : 'ghost'}
              size="icon"
              className="size-8 shrink-0"
              aria-label="Desplazar gráfico"
              aria-pressed={tool === null}
              title="Arrastrá para desplazarte por el gráfico"
              onClick={() => onToolChange(null)}
            >
              <Hand className="size-4" aria-hidden />
            </Button>
          </TooltipTrigger>
          <TooltipContent side="bottom">Arrastrá para desplazarte por el gráfico</TooltipContent>
        </Tooltip>
        <span className="mx-1 h-5 w-px shrink-0 bg-border" aria-hidden />
        <span id={helpId} className="sr-only">
          {help}
        </span>
        {TOOLS.map(({ id, label, hint, Icon }) => {
          const pressed = tool === id
          const description = [hint, 'Esc para salir.', help].filter(Boolean).join(' ')
          return (
            <Tooltip key={id}>
              <TooltipTrigger asChild>
                <span
                  className="inline-flex shrink-0"
                  tabIndex={enabled ? undefined : 0}
                  aria-label={enabled ? undefined : `${label}. ${description}`}
                >
                  <Button
                    type="button"
                    variant={pressed ? 'default' : 'ghost'}
                    size="icon"
                    className="size-8 shrink-0"
                    disabled={!enabled}
                    aria-pressed={pressed}
                    aria-label={label}
                    title={description}
                    aria-describedby={help ? helpId : undefined}
                    onClick={() => onToolChange(pressed ? null : id)}
                  >
                    <Icon className="size-4" aria-hidden />
                  </Button>
                </span>
              </TooltipTrigger>
              <TooltipContent side="bottom" className="max-w-xs">
                <span className="font-medium">{label}</span>
                <span className="mt-1 block">{description}</span>
              </TooltipContent>
            </Tooltip>
          )
        })}
        <Button
          type="button"
          variant="ghost"
          size="sm"
          className="h-8 shrink-0 gap-1"
          disabled={measurementCount === 0}
          onClick={onClear}
          aria-label={`Borrar mediciones (${measurementCount})`}
          title="Borrar todas las mediciones"
        >
          <Eraser className="size-4" aria-hidden />
          <span className="tabular-nums">{measurementCount}</span>
        </Button>
      </div>
    </TooltipProvider>
  )
}
