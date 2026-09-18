import { useCallback, useState } from 'react'

import {
  DEFAULT_AMPLITUDE,
  DEFAULT_PAPER_SPEED,
  type Amplitude,
  type PaperSpeed,
} from '../paperScale'

export interface PaperScaleState {
  paperSpeed: PaperSpeed
  amplitude: Amplitude
  setPaperSpeed: (value: PaperSpeed) => void
  setAmplitude: (value: Amplitude) => void
  /** `false` cuando el zoom libre corrió el trazado de la escala declarada. */
  onScale: boolean
  setOnScale: (value: boolean) => void
}

/**
 * Calibración del visor, compartida por el gráfico y su barra de controles.
 *
 * Vive en un hook y no adentro de `ECGViewer` porque la barra de calibración es
 * un hermano del gráfico, no un hijo: el mismo estado lo consumen el visor de la
 * solapa, el de pantalla completa y el informe imprimible.
 *
 * El arranque es siempre 25 mm/s · 10 mm/mV —el estándar de diagnóstico para
 * adultos— y no lo último que el usuario eligió. Una preferencia recordada haría
 * que un médico abriera un estudio a 50 mm/s sin haberlo pedido, y la escala es
 * justamente lo que no puede sorprender.
 */
export function usePaperScale(): PaperScaleState {
  const [paperSpeed, setPaperSpeed] = useState<PaperSpeed>(DEFAULT_PAPER_SPEED)
  const [amplitude, setAmplitude] = useState<Amplitude>(DEFAULT_AMPLITUDE)
  const [onScale, setOnScale] = useState(true)

  // Cambiar la calibración devuelve el trazado a la escala por definición: el
  // viewer reencuadra el eje contra la escala nueva.
  const changePaperSpeed = useCallback((value: PaperSpeed) => {
    setPaperSpeed(value)
    setOnScale(true)
  }, [])
  const changeAmplitude = useCallback((value: Amplitude) => {
    setAmplitude(value)
    setOnScale(true)
  }, [])

  return {
    paperSpeed,
    amplitude,
    setPaperSpeed: changePaperSpeed,
    setAmplitude: changeAmplitude,
    onScale,
    setOnScale,
  }
}
