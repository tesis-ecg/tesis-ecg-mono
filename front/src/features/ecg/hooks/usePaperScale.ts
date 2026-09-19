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

interface PaperScaleInitialValues {
  paperSpeed?: PaperSpeed
  amplitude?: Amplitude
}

/**
 * Calibración del visor, compartida por el gráfico y su barra de controles.
 *
 * Vive en un hook y no adentro de `ECGViewer` porque la barra de calibración es
 * un hermano del gráfico, no un hijo: el mismo estado lo consumen el visor de la
 * solapa, el de pantalla completa y el informe imprimible.
 *
 * Por defecto arranca en 25 mm/s · 10 mm/mV —el estándar de diagnóstico para
 * adultos—, aunque una pantalla puede declarar otra calibración inicial. Nunca
 * recuerda la última elección: abrir un estudio con una escala heredada de otra
 * sesión sería exactamente la sorpresa que esta barra busca evitar.
 */
export function usePaperScale(initialValues: PaperScaleInitialValues = {}): PaperScaleState {
  const [paperSpeed, setPaperSpeed] = useState<PaperSpeed>(
    initialValues.paperSpeed ?? DEFAULT_PAPER_SPEED,
  )
  const [amplitude, setAmplitude] = useState<Amplitude>(
    initialValues.amplitude ?? DEFAULT_AMPLITUDE,
  )
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
