import { RulerDimensionLine } from 'lucide-react'

import { Button } from '@/components/ui/button'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { cn } from '@/lib/utils'

import {
  AMPLITUDES,
  AUTO_AMPLITUDE,
  PAPER_SPEEDS,
  amplitudeLabel,
  type AmplitudeMode,
  type PaperSpeed,
} from '../paperScale'

interface ECGPaperControlsProps {
  paperSpeed: PaperSpeed
  amplitude: AmplitudeMode
  onPaperSpeedChange: (value: PaperSpeed) => void
  onAmplitudeChange: (value: AmplitudeMode) => void
  /**
   * `false` cuando el zoom libre corrió el trazado de la escala declarada. El
   * rótulo lo dice y aparece el botón para volver.
   */
  onScale: boolean
  onResetScale: () => void
  className?: string
}

/**
 * Barra de calibración del visor: ganancia, barrido y el rótulo de la escala.
 *
 * Existe porque un ECG se lee sobre una escala fija y hasta ahora el visor no
 * declaraba ninguna. Los pasos no son libres a propósito — son los de un
 * electrocardiógrafo (5/10/20 mm/mV y 25/50 mm/s) — y el rótulo en pantalla es
 * la misma convención: cualquier trazado impreso o mostrado lleva escrita su
 * calibración, porque sin ella los milímetros no significan nada.
 *
 * El zoom libre sigue existiendo para navegar. Lo que no puede pasar es que se
 * mida sobre él creyendo que es la escala estándar, y por eso el rótulo cambia a
 * "escala libre" en cuanto el viewport deja de corresponder.
 *
 * La única excepción a los pasos fijos es la amplitud automática: el rango
 * vertical sigue a la señal para poder verla entera cuando se sale de cualquier
 * ganancia (pruebas de banco, offsets grandes). El rótulo lo nombra así y no con
 * mm/mV, porque en ese modo no hay ganancia que declarar.
 */
export function ECGPaperControls({
  paperSpeed,
  amplitude,
  onPaperSpeedChange,
  onAmplitudeChange,
  onScale,
  onResetScale,
  className,
}: ECGPaperControlsProps) {
  return (
    <div className={cn('flex flex-wrap items-center gap-2', className)}>
      <Select
        value={String(paperSpeed)}
        onValueChange={(value) => onPaperSpeedChange(Number(value) as PaperSpeed)}
      >
        <SelectTrigger size="sm" className="w-[110px]" aria-label="Velocidad de barrido">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {PAPER_SPEEDS.map((speed) => (
            <SelectItem key={speed} value={String(speed)}>
              {speed} mm/s
            </SelectItem>
          ))}
        </SelectContent>
      </Select>

      <Select
        value={String(amplitude)}
        onValueChange={(value) =>
          onAmplitudeChange(
            value === AUTO_AMPLITUDE ? AUTO_AMPLITUDE : (Number(value) as AmplitudeMode),
          )
        }
      >
        <SelectTrigger size="sm" className="w-[180px]" aria-label="Ganancia">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {AMPLITUDES.map((gain) => (
            <SelectItem key={gain} value={String(gain)}>
              {amplitudeLabel(gain)}
            </SelectItem>
          ))}
          <SelectItem value={AUTO_AMPLITUDE}>{amplitudeLabel(AUTO_AMPLITUDE)}</SelectItem>
        </SelectContent>
      </Select>

      {onScale ? (
        <span
          className="text-fg-muted inline-flex items-center gap-1.5 text-sm tabular-nums"
          data-testid="ecg-scale-label"
        >
          <RulerDimensionLine className="size-4" aria-hidden />
          {paperSpeed} mm/s · {amplitudeLabel(amplitude)}
        </span>
      ) : (
        <Button
          variant="secondary"
          size="sm"
          onClick={onResetScale}
          data-testid="ecg-scale-label"
          title="El zoom libre sirve para navegar, no para medir"
        >
          Escala libre — volver a {paperSpeed} mm/s · {amplitudeLabel(amplitude)}
        </Button>
      )}
    </div>
  )
}
